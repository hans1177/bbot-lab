# -*- coding: utf-8 -*-
# 파일명: strategy_lab/v3_baseline_research.py
"""Measure the current approved MAIN with the same V3 exact research engine.

This module never changes LIVE. It exists so challenger-vs-current comparisons use
identical cache, simulator, chronological partitions and cost stress semantics.
"""
from __future__ import annotations
import json, os, subprocess, sys, time
from pathlib import Path

ROOT=Path(os.getenv("BBOT_LOCAL_ROOT",r"C:\my\bottest"))
LAB=ROOT/"strategy_lab"; RES=LAB/"results"/"v3"; GENERATED=RES/"generated_baselines"; RUNNER=LAB/"run_strategy.py"
sys.path.insert(0,str(LAB))
import v3_research_knowledge as knowledge
import v3_strategy_research as single

SUPPORTED_MATERIALS={"DIV","EMA_CROSS","MACD","OBV","SAR","SHAPE","STOCH"}


def _config(): return single.load(ROOT/"strategy_config.json",{})

def _baseline_key(tf,strategy): return f"baseline:{tf.upper()}:{strategy.upper()}"

def _wrapper(tf,strategies):
    tf=tf.upper(); strategies=[str(x).upper() for x in strategies]
    bad=[x for x in strategies if x not in SUPPORTED_MATERIALS]

    if bad:
        # REGISTRY_ASSIST_FILE_BASELINE_V1
        #
        # Legacy material strategies continue below unchanged.
        # A versioned ASSIST_FILE strategy is already a complete
        # strategy and must be evaluated from its registered source.

        if len(strategies) != 1 or len(bad) != 1:
            raise RuntimeError(
                "BASELINE_MIXED_STRATEGY_UNSUPPORTED:"
                + repr(strategies)
            )

        strategy = strategies[0]
        cfg = _config()

        registry = (
            cfg.get("strategy_registry")
            or {}
        )

        row = registry.get(strategy)

        if not isinstance(row, dict):
            row = next(
                (
                    value
                    for key, value
                    in registry.items()
                    if str(key).upper() == strategy
                    and isinstance(value, dict)
                ),
                None,
            )

        if not isinstance(row, dict):
            raise RuntimeError(
                "BASELINE_REGISTRY_ENTRY_MISSING:"
                + strategy
            )

        engine = str(
            row.get("engine")
            or ""
        ).upper()

        if engine != "ASSIST_FILE":
            raise RuntimeError(
                "BASELINE_REGISTRY_ENGINE_UNSUPPORTED:"
                + strategy
                + ":"
                + engine
            )

        filename = str(
            row.get("file")
            or ""
        ).strip()

        material_key = str(
            row.get("material_key")
            or ""
        ).strip().lower()

        if filename and (
            Path(filename).name != filename
            or "/" in filename
            or "\\" in filename
        ):
            raise RuntimeError(
                "BASELINE_REGISTRY_FILENAME_INVALID:"
                + repr(filename)
            )

        candidates = []

        if material_key:
            candidates.extend(
                [
                    RES
                    / "generated_combinations"
                    / (material_key + ".py"),

                    RES
                    / "generated_safe_grammar"
                    / (material_key + ".py"),
                ]
            )

        if filename:
            # DEPLOY_READY_REGISTRY_BASELINE_SOURCE_V1
            #
            # A validated/portable exact strategy source may already have
            # been materialized under strategy_lab/deploy_ready. Resolve
            # that immutable existing source before falling back to the
            # legacy assist/strategies location. No source is generated,
            # copied, or rewritten here.
            candidates.extend(
                [
                    ROOT
                    / "strategy_lab"
                    / "deploy_ready"
                    / filename,

                    ROOT
                    / "assist"
                    / "strategies"
                    / filename,
                ]
            )

        source = next(
            (
                candidate
                for candidate in candidates
                if candidate.is_file()
            ),
            None,
        )

        if source is None:
            raise RuntimeError(
                "BASELINE_REGISTRY_SOURCE_MISSING:"
                + strategy
                + ":"
                + repr(
                    [
                        str(x)
                        for x in candidates
                    ]
                )
            )

        source_text = source.read_text(
            encoding="utf-8-sig",
            errors="strict",
        )

        compile(
            source_text,
            str(source),
            "exec",
        )

        if (
            "generate_signals"
            not in source_text
            and
            "def signal"
            not in source_text
        ):
            raise RuntimeError(
                "BASELINE_REGISTRY_SIGNAL_CONTRACT_MISSING:"
                + str(source)
            )

        return (
            source,
            strategy,
            strategy,
        )
    GENERATED.mkdir(parents=True,exist_ok=True)
    combo="+".join(strategies); name=f"V3_BASELINE_{tf}_{combo.replace('+','_')}_V1"
    out=GENERATED/(name.lower()+".py")
    code=f'''# -*- coding: utf-8 -*-\n# AUTO-GENERATED CURRENT MAIN BASELINE - RESEARCH ONLY\nimport numpy as np, pandas as pd\nSTRATEGY_META={{"name":{name!r},"development_mode":"CURRENT_MAIN_BASELINE","data_mode":"CACHE_MATERIALS","allowed_timeframes":[{tf!r}],"components":{strategies!r}}}\ndef _material(df,tf,material):\n p=str(tf).lower(); source="ema" if material=="EMA_CROSS" else material.lower(); lc=f"{{p}}_{{source}}_long"; sc=f"{{p}}_{{source}}_short"\n if lc not in df.columns or sc not in df.columns: raise KeyError(f"required material columns missing: {{lc}}, {{sc}}")\n lv=pd.to_numeric(df[lc],errors="coerce").fillna(0).to_numpy(float); sv=pd.to_numeric(df[sc],errors="coerce").fillna(0).to_numpy(float)\n state=np.zeros(len(df),dtype=np.int8); state[(lv>sv)&(lv>0)]=1; state[(sv>lv)&(sv>0)]=-1\n if material!="EMA_CROSS": return state\n prev=np.roll(state,1);\n if len(prev): prev[0]=state[0]\n event=np.zeros(len(df),dtype=np.int8); event[(state==1)&(prev!=1)]=1; event[(state==-1)&(prev!=-1)]=-1\n valid_col=f"{{p}}_ema_valid"\n if valid_col in df.columns:\n  valid=pd.to_numeric(df[valid_col],errors="coerce").fillna(0).to_numpy(float)>0; event[~valid]=0\n return event\ndef generate_signals(df,tf):\n xs=[_material(df,tf,x) for x in STRATEGY_META["components"]]; out=np.zeros(len(df),dtype=np.int8)\n if not xs: return out\n long=np.ones(len(df),dtype=bool); short=np.ones(len(df),dtype=bool)\n for x in xs: long &= (x>0); short &= (x<0)\n out[long]=1; out[short]=-1; return out\n'''
    out.write_text(code,encoding="utf-8")
    return out,name,combo


def current_identity(tf):
    cfg=_config(); row=((cfg.get("frames") or {}).get(tf.upper()) or {})
    if not bool(row.get("enabled")): return None
    strategies=[str(x).upper() for x in (row.get("strategies") or []) if str(x).strip()]
    if not strategies: return None
    return {
        "timeframe":tf.upper(),
        "strategies":strategies,
        "strategy":"+".join(strategies),
        "strategy_version":str(row.get("strategy_version") or f"SV_{tf.upper()}_CONFIG_CURRENT"),
        "deployment_tier":str(row.get("deployment_tier") or "").upper(),
    }


def get(tf):
    ident=current_identity(tf)
    if not ident:return None
    k,_=knowledge.ensure(); return (k.get("records") or {}).get(_baseline_key(tf,ident["strategy"]))


def run(tf="12H"):
    tf=tf.upper(); ident=current_identity(tf)
    if not ident:return {"status":"NO_ACTIVE_MAIN","timeframe":tf}
    prior=get(tf)

    # BASELINE_EVIDENCE_UPGRADE_RERUN_REASON_V1
    #
    # If symbol_evidence_written already exists, even when it is 0,
    # this evidence-contract upgrade has already been attempted.
    if (
        isinstance(prior,dict)
        and prior.get("status")=="MEASURED"
        and "symbol_evidence_written" in prior
    ):
        return prior

    rerun_reason=None

    if (
        isinstance(prior,dict)
        and prior.get("status")=="MEASURED"
        and "symbol_evidence_written" not in prior
    ):
        rerun_reason="EVIDENCE_CONTRACT_UPGRADE_SYMBOL_LEVEL_V1"

    wrapper,name,combo=_wrapper(tf,ident["strategies"])

    trial=knowledge.reserve_trial(
        f"V3_BASELINE_{tf}_{combo}",
        combo,
        tf,
        "CURRENT_MAIN_BASELINE",
        rerun_reason=rerun_reason,
    )
    env=os.environ.copy(); env["BBOT_LOCAL_ROOT"]=str(ROOT); start=time.time()
    p=subprocess.run([sys.executable,str(RUNNER),str(wrapper)],cwd=str(ROOT),env=env,text=True,encoding="utf-8",errors="replace",stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    if p.returncode!=0:
        knowledge.complete_trial(trial,"RUN_STRATEGY_FAIL","FAIL","UNKNOWN")
        row={"status":"FAIL","reason":"RUN_STRATEGY_FAIL","timeframe":tf,"strategy":combo,"trial":trial,"output_tail":p.stdout[-4000:]}
        knowledge.record_result(_baseline_key(tf,combo),row,failed=True); return row
    result=single.find_result(name,start)
    if result is None:
        knowledge.complete_trial(trial,"RESULT_NOT_FOUND","FAIL","UNKNOWN"); return {"status":"FAIL","reason":"RESULT_NOT_FOUND","timeframe":tf,"strategy":combo,"trial":trial}
    rep=single.load(result,{}); ev=single.evaluate(rep,tf)
    cur=(((rep.get("datasets") or {}).get("CURRENT_35_FROM_2020") or {}).get("timeframes") or {}).get(tf) or {}
    rb=cur.get("v3_robustness") or {}; sm=cur.get("summary") or {}
    edge=single.finite((rb.get("walk_forward") or {}).get("avg_net_pct")); hold=single.finite(sm.get("avg_hold_hours")); loss=single.finite(sm.get("avg_loss_pct"))
    edge_dec=(edge/100.0) if edge is not None else None; loss_dec=abs(loss/100.0) if loss is not None else None
    cte=(edge_dec/hold) if edge_dec is not None and hold and hold>0 else None
    common={"strategy":combo,"timeframe":tf,"strategy_version":ident.get("strategy_version") or "CONFIG_CURRENT","expected_return":edge_dec,"expected_loss":loss_dec,"expected_holding_time_hours":hold,"capital_time_efficiency":cte,"expected_account_contribution":edge_dec,"confidence":None,"confidence_level":"INITIAL_BACKTEST_ONLY","expectation_basis":"NET_EXPECTED_RETURN_PER_UNIT_CAPITAL_SAME_ENGINE","evidence_summary":{"backtest":"EXACT_LONG_CACHE","walk_forward":"CHRONOLOGICAL","cost_stress":"2X"}}
    symbol_rows=cur.get("symbol_evidence") if isinstance(cur.get("symbol_evidence"),dict) else {}
    written=0
    for sym,se in symbol_rows.items():
        if not isinstance(se,dict): continue
        ssm=se.get("summary") if isinstance(se.get("summary"),dict) else {}; srb=se.get("v3_robustness") if isinstance(se.get("v3_robustness"),dict) else {}
        swf=srb.get("walk_forward") if isinstance(srb.get("walk_forward"),dict) else {}
        sedge=single.finite(swf.get("avg_net_pct")); shold=single.finite(ssm.get("avg_hold_hours")); sloss=single.finite(ssm.get("avg_loss_pct")); strades=int(ssm.get("trades") or 0)
        if sedge is None or shold is None or shold<=0 or strades<=0: continue
        sedge_dec=sedge/100.0; scte=sedge_dec/shold
        rec=dict(common); rec.update({"symbol":str(sym).upper(),"expected_return":sedge_dec,"expected_loss":abs(sloss/100.0) if sloss is not None else None,"expected_holding_time_hours":shold,"capital_time_efficiency":scte,"expected_account_contribution":sedge_dec,"sample_trades":strades,"status":"MEASURED"})
        knowledge.record_result(f"runtime:{combo}:{tf}:{str(sym).upper()}",rec,failed=False); written+=1
    row={"status":"MEASURED","timeframe":tf,"strategy":combo,"trial":trial,"evaluation":ev,"result_json":str(result.relative_to(ROOT)),"sealed_blind_single":rb.get("sealed_blind_single") or {},"sealed_blind_combo":rb.get("sealed_blind_combo") or {},"metric_contract":"SAME_RUN_STRATEGY_EXACT_CACHE_CHRONO_PARTITIONS_COST_STRESS_2X","deployment_tier":ident.get("deployment_tier"),"symbol_evidence_written":written,**common}
    knowledge.complete_trial(trial,str(result.relative_to(ROOT)),"MEASURED","LOW"); knowledge.record_result(_baseline_key(tf,combo),row,failed=False)
    return row

# -----------------------------------------------------------------------------
# 직접 실행 진입점
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    timeframe = sys.argv[1] if len(sys.argv) > 1 else "12H"
    print(json.dumps(run(timeframe), ensure_ascii=False))
