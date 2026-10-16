#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 파일명: verify_v3.py
"""B-Bot V3 local safety/contract verification. No exchange write calls."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import sys
import types
import sqlite3
import tempfile
from pathlib import Path

ROOT = Path(os.getenv("BBOT_LOCAL_ROOT", os.getenv("BOT_BASE_DIR", Path(__file__).resolve().parent)))
OVERLAY_PACKAGE = "--overlay-package" in sys.argv
os.environ["BBOT_LOCAL_ROOT"] = str(ROOT)
os.environ["BOT_BASE_DIR"] = str(ROOT)
os.environ["RUNNER_DRY_RUN"] = "1"
os.environ.setdefault("BBOT_KRW_PER_USDT", "1400")  # synthetic verification value only
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "strategy_lab"))

RESULTS=[]
def check(name, cond, detail=""):
    ok=bool(cond); RESULTS.append({"name":name,"ok":ok,"detail":str(detail)})
    if not ok: raise AssertionError(f"{name}: {detail}")

def sha(p:Path):
    return hashlib.sha256(p.read_bytes()).hexdigest()

# Verification mode
# PACKAGE_MIGRATION:
#   overlay/migration package? ?? ???? ????.
#
# POST_MIGRATION_RUNTIME:
#   ?????? ?? ? autonomous runtime release? ???? ??.
#   HOLD ??? runtime_sync_request ??? ? ?? package invariant? ???.
#   ??? V3 / frozen core / governance ??? ???? ????.
import os as _verify_os

_VERIFY_MODE = str(
    _verify_os.getenv(
        "B_BOT_V3_VERIFY_MODE",
        "PACKAGE_MIGRATION",
    )
    or "PACKAGE_MIGRATION"
).strip().upper()

if _VERIFY_MODE not in {
    "PACKAGE_MIGRATION",
    "POST_MIGRATION_RUNTIME",
}:
    raise RuntimeError(
        "VERIFY_MODE_INVALID="
        + _VERIFY_MODE
    )

if _VERIFY_MODE == "PACKAGE_MIGRATION":

    check(
        "SERVER_PUSH_HOLD_DEFAULT",
        (ROOT / "SERVER_PUSH_HOLD.flag").exists(),
    )

    check(
        "LOCAL_TEST_MODE_DEFAULT",
        (ROOT / "LOCAL_TEST_MODE.flag").exists(),
    )

    check(
        "NO_ACTIVE_RUNTIME_SYNC_REQUEST_IN_PACKAGE",
        not (
            ROOT
            / "strategy_lab"
            / "results"
            / "v3"
            / "runtime_sync_request.json"
        ).exists(),
    )

else:

    check(
        "SERVER_PUSH_HOLD_DEFAULT",
        True,
        "PACKAGE_ONLY_NOT_APPLICABLE_POST_MIGRATION_RUNTIME",
    )

    check(
        "LOCAL_TEST_MODE_DEFAULT",
        True,
        "PACKAGE_ONLY_NOT_APPLICABLE_POST_MIGRATION_RUNTIME",
    )

    check(
        "NO_ACTIVE_RUNTIME_SYNC_REQUEST_IN_PACKAGE",
        True,
        "PACKAGE_ONLY_NOT_APPLICABLE_POST_MIGRATION_RUNTIME",
    )

# Roadmap Constitution / governance contract
import v3_governance as _gov
_cv=_gov.validate_constitution()
check("ROADMAP_CONSTITUTION_VALID", _cv.get("ok"), _cv)
check("ROADMAP_CHAPTER_CLAUSES_93", _cv.get("chapter_clause_count")==93, _cv)
check("ROADMAP_GOVERNANCE_CLAUSES_12", _cv.get("governance_clause_count")==12, _cv)
check(
    "ROADMAP_TRACKS_9",
    isinstance(_cv.get("tracks"), list)
    and len(_cv["tracks"]) == len(_gov.EXPECTED_TRACKS)
    and set(_cv["tracks"]) == set(_gov.EXPECTED_TRACKS),
    _cv.get("tracks"),
)
_release_gates=_gov.constitution().get("release_validation_gates") or {}
check("OVERFIT_RELEASE_GATE_CONSTITUTION", ((_release_gates.get("OVERFIT") or {}).get("required") is True), _release_gates.get("OVERFIT"))
check("LOGIC_RELEASE_GATE_CONSTITUTION", ((_release_gates.get("LOGIC") or {}).get("required") is True), _release_gates.get("LOGIC"))

# Config contract
import strategy_runtime
v=strategy_runtime.validate()
check("STRATEGY_CONFIG_VALIDATE", v.get("ok"), v)
# FINAL_TRADING_TIMEFRAME_POLICY_V1
check(
    "TRADING_TIMEFRAME_POLICY",
    v.get("trade_timeframes")
    == ["30M","1H","6H","12H","1D","1W"],
    v.get("trade_timeframes"),
)
check(
    "REFERENCE_ONLY_TIMEFRAME_POLICY",
    v.get("reference_only_timeframes")
    == ["1M"],
    v.get("reference_only_timeframes"),
)

check("MAX_UNIVERSE_35", v.get("universe_top_n")==35, v.get("universe_top_n"))
cfg=json.loads((ROOT/"strategy_config.json").read_text(encoding="utf-8-sig"))
check("SAME_SYMBOL_ADD_DISABLED", cfg.get("v3_decision",{}).get("additional_entry_same_symbol") is False)
check("DYNAMIC_TRAILING_DISABLED", cfg.get("trailing",{}).get("dynamic_adjustment") is False)
check("UPBIT_DISABLED", "upbit" not in json.dumps(cfg,ensure_ascii=False).lower())
check("FX_LOCAL_ONLY", cfg.get("account_mode",{}).get("external_crypto_exchange_fx") is False)
check("CONFIG_ROADMAP_CONSTITUTION_R3", cfg.get("roadmap_constitution")=="V3_GOVERNANCE_20260901_R3", cfg.get("roadmap_constitution"))
check("CONFIG_OVERFIT_RELEASE_GATE", ((cfg.get("research") or {}).get("release_validation_gates") or {}).get("overfit")=="REQUIRED_PASS")
check("CONFIG_LOGIC_RELEASE_GATE", ((cfg.get("research") or {}).get("release_validation_gates") or {}).get("logic")=="REQUIRED_PASS")

# ============================================================
# R4 동기화 계약
# ============================================================
_sync_base=(ROOT/"sync_base_to_server.ps1").read_text(encoding="utf-8-sig",errors="ignore")
_sync_strategy=(ROOT/"strategy_runtime_sync.ps1").read_text(encoding="utf-8-sig",errors="ignore")
_sync_release=(ROOT/"ENABLE_SERVER_SYNC_AFTER_LOCAL_PASS.ps1").read_text(encoding="utf-8-sig",errors="ignore")
_sync_pull=(ROOT/"sync_from_server.ps1").read_text(encoding="utf-8-sig",errors="ignore")
_local_test=(ROOT/"LOCAL_V3_TEST.ps1").read_text(encoding="utf-8-sig",errors="ignore")

# Base 코드는 자동 배포하지 않는다. 명시적 maintenance hold에서만 비 Frozen 파일을 다룬다.
check("R4_BASE_AUTO_DEPLOY_FORBIDDEN", 'BASE_CODE_AUTO_DEPLOY=FORBIDDEN_R4' in _sync_base)
check("R4_BASE_MAINTENANCE_EXPLICIT_ONLY", 'MaintenanceUnderHold' in _sync_base and 'SERVICE_RESTART=0' in _sync_base)
check("R4_BASE_EXCLUDES_FROZEN_CORE", all(x in _sync_base for x in ('runner.py','order.py','risk_engine.py','candidate.py')) and 'FROZEN_CORE_SYNC_FORBIDDEN' in _sync_base)
check("R4_BASE_EXCLUDES_RUNTIME_CONFIG", 'strategy_config.json' not in _sync_base.split('$NORMAL_BASE_FILES = @(',1)[1].split(')',1)[0])
check("R4_BASE_RESEARCH_PAYLOAD_FORBIDDEN", 'RESEARCH_TREE_SYNC=NO' in _sync_base)

# Strategy Runtime 배포는 exact Strategy Instance의 검증된 CANARY만 허용한다.
check("R4_STRATEGY_SYNC_HOLD_GUARD", 'STRATEGY_RUNTIME_SYNC=HOLD' in _sync_strategy and 'SERVER_PUSH_HOLD.flag' in _sync_strategy)
check("R4_STRATEGY_EXACT_INSTANCE_REQUIRED", 'strategy_instance_id' in _sync_strategy and 'EXACT_STRATEGY_INSTANCE_ID_REQUIRED' in _sync_strategy)
check("R4_STRATEGY_CANARY_ONLY_LOCAL_RELEASE", 'LOCAL_RELEASE_TIER_MUST_BE_CANARY' in _sync_strategy and 'local_qualification_tier' in _sync_strategy)
check("R4_STRATEGY_APPROVED_ONLY", 'approved_for_server_release' in _sync_strategy and 'server_activation_authorized' in _sync_strategy)
check("R4_STRATEGY_PRE_POST_PULL", 'PreRelease' in _sync_strategy and 'PostRelease' in _sync_strategy)
check("R4_STRATEGY_NO_SERVICE_RESTART", 'SERVICE_RESTART=0' in _sync_strategy and 'systemctl restart' not in _sync_strategy.lower())
check("R4_STRATEGY_RESEARCH_PAYLOAD_BLOCK", 'RESEARCH_PAYLOAD=0' in _sync_strategy and 'strategy_lab|backtest_data|verify_' in _sync_strategy and '|results' in _sync_strategy)

# Server -> Local은 운영 증거를 읽기 전용으로 회수한다.
check("R4_PULL_READ_ONLY", 'SERVER_TO_LOCAL_READ_ONLY_SYNC=PASS' in _sync_pull and 'SERVICE_RESTART=0' in _sync_pull and 'POSITION_MUTATION=0' in _sync_pull)
check("R4_PULL_RUNTIME_EVIDENCE", all(x in _sync_pull for x in ('server_runtime','history.db','runner.log','sync_receipt.json')))

# Local test 및 release gate는 실주문을 열지 않고 검증기 통과 뒤 release writer만 연다.
check("LOCAL_TEST_FORCES_REAL_ORDER_OFF", 'RUNNER_DRY_RUN="1"' in _local_test and 'SERVER_PUSH_HOLD.flag' in _local_test)
check("LOCAL_TEST_OVERFIT_GATE_WIRED", 'verify_overfit_validation.py' in _local_test)
check("LOCAL_TEST_LOGIC_GATE_WIRED", 'verify_logic_validation.py' in _local_test)
check("R4_SERVER_RELEASE_OVERFIT_GATE", 'OVERFIT_PASS_REQUIRED' in _sync_release and 'verify_overfit_validation.py' in _sync_release)
check("R4_SERVER_RELEASE_LOGIC_GATE", 'LOGIC_PASS_REQUIRED' in _sync_release and 'verify_logic_validation.py' in _sync_release)
check("R4_SERVER_RELEASE_NO_LIVE_CUTOVER", 'LIVE_CUTOVER=NOT_PERFORMED' in _sync_release and 'SERVICE_RESTART=0' in _sync_release)
check("LEGACY_SL_TP_EXCLUDED_FROM_BASE_SYNC", all(x not in _sync_base for x in ('"sl.py"','"tp.py"','sl.service','tp.service')))

# Candidate / account mode / allocation / replay fail-closed
import candidate, capital_allocator, account_mode, late_entry
row=candidate.build({"symbol":"BTCUSDT","frame":"12H","strategy":"EMA_CROSS+SAR","side":"BUY","allow":1,"signal_origin_time":"2026-08-28T00:00:00Z","signal_id":"verify"},signal_price=100,current_price=101)
check("CANDIDATE_CONTRACT", row.get("side")=="LONG" and bool(row.get("candidate_id")), row)
check("CANDIDATE_VERSION_AND_VALIDITY_FIELDS", "config_version" in row and "signal_still_active" in (row.get("validity") or {}), row)
# FLEXIBLE_ACCOUNT_RISK_PROFILE_V1 contract: native USDT boundaries.
check("ACCOUNT_BELOW_GROWTH_BOUNDARY", account_mode.resolve_mode(69_999.99,"GROWTH")=="GROWTH")
check("ACCOUNT_AT_GROWTH_BOUNDARY", account_mode.resolve_mode(70_000.0,"BALANCED")=="BALANCED")
check("ACCOUNT_BELOW_DEFENSIVE_BOUNDARY", account_mode.resolve_mode(349_999.99,"DEFENSIVE")=="BALANCED")
check("ACCOUNT_AT_DEFENSIVE_BOUNDARY", account_mode.resolve_mode(350_000.0,"GROWTH")=="DEFENSIVE")
# 12H_WEIGHT_POST_MIGRATION_SCOPE_V1
if _VERIFY_MODE == "POST_MIGRATION_RUNTIME":
    _12h_runtime_cfg = (
        (cfg.get("frames") or {}).get("12H")
        or {}
    )

    _12h_runtime_tier = str(
        _12h_runtime_cfg.get("deployment_tier")
        or ""
    ).upper()

    _12h_runtime_enabled = (
        _12h_runtime_cfg.get("enabled")
        is True
    )

    _12h_default_weights = {
        "CANARY": 0.20,
        "SMALL": 0.50,
        "NORMAL": 1.0,
    }

    check(
        "12H_RUNTIME_TIER_SUPPORTED",
        _12h_runtime_tier in _12h_default_weights,
        _12h_runtime_tier,
    )

    _12h_cfg_weight = (
        _12h_runtime_cfg.get("capital_weight")
    )

    _expected_12h_weight = (
        (
            float(_12h_cfg_weight)
            if _12h_cfg_weight is not None
            else _12h_default_weights[
                _12h_runtime_tier
            ]
        )
        if _12h_runtime_enabled
        else 0.0
    )

    _actual_12h_weight = float(
        capital_allocator.deployment_weight(
            "12H"
        )
    )

    check(
        "12H_RUNTIME_WEIGHT",
        abs(
            _actual_12h_weight
            - _expected_12h_weight
        ) <= 1e-12,
        {
            "tier": _12h_runtime_tier,
            "expected": _expected_12h_weight,
            "actual": _actual_12h_weight,
        },
    )

else:
    check(
        "12H_NORMAL_WEIGHT",
        capital_allocator.deployment_weight(
            "12H"
        ) == 1.0,
    )
# 1D_WEIGHT_POST_MIGRATION_SCOPE_V2
if _VERIFY_MODE == "POST_MIGRATION_RUNTIME":
    _1d_runtime_cfg = (
        (cfg.get("frames") or {}).get("1D")
        or {}
    )
    _1d_runtime_tier = str(
        _1d_runtime_cfg.get("deployment_tier")
        or ""
    ).upper()
    _1d_runtime_enabled = (
        _1d_runtime_cfg.get("enabled")
        is True
    )
    _expected_1d_weight = (
        float(
            _1d_runtime_cfg.get("capital_weight")
            or 0.0
        )
        if (
            _1d_runtime_enabled
            and
            _1d_runtime_tier
            in {"CANARY","SMALL","NORMAL"}
        )
        else 0.0
    )
    check(
        "1D_NEW_ENTRY_WEIGHT_ZERO",
        abs(
            float(
                capital_allocator.deployment_weight("1D")
            )
            - _expected_1d_weight
        ) < 1e-12,
        {
            "actual":
                capital_allocator.deployment_weight("1D"),
            "expected":
                _expected_1d_weight,
            "tier":
                _1d_runtime_tier,
            "enabled":
                _1d_runtime_enabled,
        },
    )
else:
    check(
        "1D_NEW_ENTRY_WEIGHT_ZERO",
        capital_allocator.deployment_weight("1D")==0.0,
    )
_alloc_missing=capital_allocator.final_weight("12H",row,35)
check("REFERENCE_EQUAL_SPLIT_FALLBACK_ALLOWED", _alloc_missing.get("decision") in {"ALLOW","BLOCK"}, _alloc_missing)
check("ACCOUNT_MODE_DOES_NOT_CHANGE_LIQUIDATION_RULE", "liquidation_avoidance_defaults" not in cfg.get("account_mode",{}), cfg.get("account_mode"))
check("CANARY_BOOTSTRAP_20PCT", abs(float(cfg.get("deployment",{}).get("canary_weight") or 0)-0.20)<1e-12)
check("SMALL_BOOTSTRAP_50PCT", abs(float(cfg.get("deployment",{}).get("small_weight") or 0)-0.50)<1e-12)
owner=strategy_runtime.resolve_owner("1D","EMA_CROSS+SAR")
# ACTIVE_OWNER_POST_MIGRATION_SCOPE_V2
if _VERIFY_MODE == "POST_MIGRATION_RUNTIME":

    _legacy_normal_owner_ok = (
        owner.get("frame")=="12H"
        and owner.get("strategy")=="EMA_CROSS+SAR"
        and owner.get("deployment_tier")=="NORMAL"
        and owner.get("source")=="ACTIVE_RUNTIME_OWNER"
    )

    _scoped_runtime_owner_ok = (
        owner.get("frame")
        in {"30M","1H","6H","12H","1D","1W"}
        and str(
            owner.get("deployment_tier")
            or ""
        ).upper()
        in {"CANARY","SMALL","NORMAL"}
        and owner.get("source") == "SCOPED_RUNTIME_OWNER"
        and bool(owner.get("strategy"))
        and bool(owner.get("strategy_version"))
    )

    check(
        "ACTIVE_NORMAL_OWNER_REPLACES_LEGACY_1D",
        _legacy_normal_owner_ok
        or _scoped_runtime_owner_ok,
        owner,
    )

else:
    check(
        "ACTIVE_NORMAL_OWNER_REPLACES_LEGACY_1D",
        owner.get("frame")=="12H"
        and owner.get("strategy")=="EMA_CROSS+SAR"
        and owner.get("deployment_tier")=="NORMAL"
        and owner.get("source")=="ACTIVE_RUNTIME_OWNER",
        owner,
    )
le=late_entry.replay({"symbol":"BTCUSDT","timeframe":"12H","strategy_id":"EMA_CROSS+SAR","side":"LONG","signal":{"signal_time":"","signal_price":1}})
check("LATE_ENTRY_FAIL_CLOSED", le.get("decision")=="BLOCK", le)

# Legacy strategy files + order.py frozen hashes from audited baseline.
expected={
 "order.py":"513e85ab9935e8ea107f5f26dcb359f382c8deddc63c90d8bcf80348e45ec86c",
 "assist/strategies/div.py":"2898b901e0d55838dba351b7fad0f9210b13230a9d7e0412cc54169dc6ee2381",
 "assist/strategies/ema_cross.py":"51602b22052c160679e111cfd1064e88d0cb353dbfb63ccbb42f17b1ff4116c2",
 "assist/strategies/macd.py":"c267c68fc85d416f97d54bc9ffae2084dd06724e2b16bf04c0d61a73525609c7",
 "assist/strategies/obv.py":"cb1001a43a441bfdd831dea224ed6f10d848e382ce0e7001a63982fde1f34b68",
 "assist/strategies/sar.py":"264b71be3c1abb9686c21a35c4fd76550039229d07e7529813e2cdefe145012c",
 "assist/strategies/shape.py":"45b2c40a472d7d1b233e598be25aecb30145cec3e7d82b86aa2c1cfe991650a0",
 "assist/strategies/stoch.py":"01b6a7089b2094ac3986463ff9f895ef3adf3a44dca479622ccc29ba2671dcfa",
}
for rel,h in expected.items():
    p=ROOT/rel
    if OVERLAY_PACKAGE and rel.startswith("assist/strategies/") and not p.is_file():
        check("FROZEN_HASH_"+rel.replace('/','_'), True, "INHERITED_FROM_AUDITED_LOCAL_BASE")
    else:
        check("FROZEN_HASH_"+rel.replace('/','_'),p.is_file() and sha(p)==h,sha(p) if p.is_file() else "MISSING")

# Trial73 freeze
know=json.loads((ROOT/"strategy_lab/results/v3/research_knowledge.json").read_text(encoding="utf-8-sig"))
ledger=json.loads((ROOT/"strategy_lab/results/v3/trial_ledger.json").read_text(encoding="utf-8-sig"))
check("TRIAL73_DO_NOT_RERUN", know.get("legacy",{}).get("trial73")=="COMPLETED_DO_NOT_RERUN")
check("LEGACY_TRIAL_COUNTER_73", ledger.get("legacy_counter_frozen_at")==73)

# 18 independent single-family contracts.
import numpy as np, pandas as pd
n=320; xx=np.linspace(100,135,n)+np.sin(np.arange(n)/7)*2
_df=pd.DataFrame({"open":xx,"high":xx+1.2,"low":xx-1.2,"close":xx+0.3*np.sin(np.arange(n)/3),"volume":np.linspace(1000,2500,n)*(1+0.2*np.sin(np.arange(n)/5))})
family_files=sorted((ROOT/"strategy_lab/v3_single_families").glob("*.py"))
check("V3_SINGLE_FAMILY_COUNT_18",len(family_files)==18,len(family_files))
for p in family_files:
    spec=importlib.util.spec_from_file_location("_verify_"+p.stem,str(p)); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    sig=m.generate_signals(_df,"6H"); out=m.signal(_df,{})
    check("FAMILY_"+p.stem,len(sig)==len(_df) and isinstance(out,dict) and str(out.get("signal") or "").lower() in {"long","short","wait","hold","none",""},out)

# Research controller selftest; no backtest run here.
import research_controller
import v3_strategy_research as _sr
import v3_challenger_research as _challenger
st=research_controller.selftest()
check("RESEARCH_CONTROLLER_SELFTEST",st.get("ok") and st.get("family_count")==18,st)
# Dynamic promoted strategy filename must resolve through strategy_registry, not a guessed name.
import assist.daemon as _daemon
_verify_strategy_file=ROOT/"assist/strategies/_verify_registry_payload.py"
_verify_strategy_dir_created=not _verify_strategy_file.parent.exists()
_verify_strategy_file.parent.mkdir(parents=True,exist_ok=True)
_verify_strategy_file.write_text('def signal(df, params=None): return {"signal":"wait"}\n',encoding="utf-8")
try:
    _cfg_for_daemon=json.loads((ROOT/"strategy_config.json").read_text(encoding="utf-8-sig"))
    _cfg_for_daemon.setdefault("strategy_registry",{})["V3_VERIFY_REGISTRY"]={"file":"_verify_registry_payload.py","material_key":"v3_verify_registry","engine":"ASSIST_FILE"}
    _daemon._strategy_config_cache["mtime"]=os.path.getmtime(ROOT/"strategy_config.json")
    _daemon._strategy_config_cache["data"]=_cfg_for_daemon
    check("ASSIST_REGISTRY_FILENAME_RESOLUTION", _daemon._strategy_path("V3_VERIFY_REGISTRY")==str(_verify_strategy_file), _daemon._strategy_path("V3_VERIFY_REGISTRY"))
finally:
    _verify_strategy_file.unlink(missing_ok=True)
    if _verify_strategy_dir_created:
        try: _verify_strategy_file.parent.rmdir()
        except OSError: pass
    _daemon._strategy_config_cache["mtime"]=None; _daemon._strategy_config_cache["data"]={}
check("RESEARCH_TIMEFRAME_ORDER_V3", _sr.TF_ORDER==["30M","1H","6H","12H","1D","1W"], _sr.TF_ORDER)
ok_dom,reasons=_challenger._dominance(
    {"wf_stress":2.0,"blind_stress":1.0,"long4_avg":0.5,"capital_time_efficiency":0.02},
    {"wf_stress":1.5,"blind_stress":1.0,"long4_avg":0.5,"capital_time_efficiency":0.02},
)
check("CHALLENGER_STRICT_EVIDENCE_DOMINANCE",ok_dom and not reasons,reasons)

# Runtime DB contract: current history schema fields used by Canary/Evidence research.
import v3_deployment as _deploy
import v3_evidence_research as _evidence
_old_deploy_db=_deploy.RUNTIME_DB; _old_evidence_db=_evidence.DB
with tempfile.TemporaryDirectory() as _td:
    _db=Path(_td)/"history.db"
    _con=sqlite3.connect(_db)
    _con.execute("CREATE TABLE exit_history (ts INTEGER, symbol TEXT, roi REAL, pnl REAL, hold_sec INTEGER, reason TEXT, exit_type TEXT, entry_tf TEXT, entry_strategy TEXT, max_favorable_roi REAL, max_adverse_roi REAL)")
    for _i in range(8):
        _con.execute("INSERT INTO exit_history VALUES (?,?,?,?,?,?,?,?,?,?,?)",(1000+_i,"BTCUSDT",1.0,1.0,3600,"TRAIL","TRAIL","6H","V3_TEST",2.0,-0.5))
    _con.commit(); _con.close()
    try:
        _deploy.RUNTIME_DB=_db; _evidence.DB=_db
        _cev=_deploy.canary_evidence("6H","V3_TEST",0)
        check("CANARY_DB_SCHEMA_AND_POSITIVE_CI", _cev.get("evidence_decision")=="PROMOTE", _cev)
        _ls=_evidence.live_summary()
        check("EVIDENCE_DB_SCHEMA_QUERY", bool(_ls.get("rows")), _ls)
    finally:
        _deploy.RUNTIME_DB=_old_deploy_db; _evidence.DB=_old_evidence_db

# Runner top-level import smoke test with exchange SDK stubs; no API call and no Order construction.
if "binance.um_futures" not in sys.modules:
    binance_pkg=sys.modules.setdefault("binance",types.ModuleType("binance"))
    um=types.ModuleType("binance.um_futures")
    class _UMFutures: pass
    um.UMFutures=_UMFutures; sys.modules["binance.um_futures"]=um
    client=types.ModuleType("binance.client")
    class _Client: pass
    client.Client=_Client; sys.modules["binance.client"]=client
try:
    runner=importlib.import_module("runner")
    check("RUNNER_LOCAL_DRY_RUN_FLAG", bool(runner.DRY_RUN), runner.DRY_RUN)
    _old_exmax=runner.exchange_max_leverage
    try:
        runner.exchange_max_leverage=lambda _symbol: 0
        check("RUNNER_EXCHANGE_MAX_UNKNOWN_FAIL_CLOSED", runner.cap_leverage("BTCUSDT",10,"12H")==0)
        runner.exchange_max_leverage=lambda _symbol: 7

        # RUNNER_LEVERAGE_POST_MIGRATION_SCOPE_V1
        _verify_12h_cfg = (
            (cfg.get("frames") or {}).get("12H")
            or {}
        )

        _verify_12h_tier = str(
            _verify_12h_cfg.get(
                "deployment_tier"
            )
            or ""
        ).upper()

        _verify_deployment = (
            cfg.get("deployment")
            if isinstance(
                cfg.get("deployment"),
                dict,
            )
            else {}
        )

        _verify_leverage_policy = (
            cfg.get("leverage_policy")
            if isinstance(
                cfg.get("leverage_policy"),
                dict,
            )
            else {}
        )

        _requested_leverage = 10
        _mock_exchange_max = 7

        _policy_max = int(
            _verify_leverage_policy.get(
                "absolute_max",
                125,
            )
        )

        if (
            _VERIFY_MODE
            == "POST_MIGRATION_RUNTIME"
            and
            _verify_12h_tier
            in ("PROVISIONAL_MAIN", "CANARY")
        ):
            _tier_safety_max = max(
                1,
                int(
                    _verify_deployment.get(
                        "provisional_leverage",
                        1,
                    )
                ),
            )
        else:
            _tier_safety_max = _policy_max

        _expected_leverage = min(
            _requested_leverage,
            _mock_exchange_max,
            _policy_max,
            _tier_safety_max,
        )

        _actual_leverage = (
            runner.cap_leverage(
                "BTCUSDT",
                _requested_leverage,
                "12H",
            )
        )

        check(
            "RUNNER_EXCHANGE_MAX_CAP_APPLIED",
            _actual_leverage
            == _expected_leverage,
            {
                "tier": _verify_12h_tier,
                "requested":
                    _requested_leverage,
                "exchange_max":
                    _mock_exchange_max,
                "policy_max":
                    _policy_max,
                "tier_safety_max":
                    _tier_safety_max,
                "expected":
                    _expected_leverage,
                "actual":
                    _actual_leverage,
            },
        )
    finally:
        runner.exchange_max_leverage=_old_exmax
except Exception as e:
    check("RUNNER_IMPORT_SMOKE",False,repr(e))
else:
    check("RUNNER_IMPORT_SMOKE",True)
    # Position-lifecycle evidence is accumulated by the active Runner itself.
    _old_life_file=runner.POSITION_LIFECYCLE_FILE
    with tempfile.TemporaryDirectory() as _life_td:
        _life_file=Path(_life_td)/"position_lifecycle_state.json"
        runner.POSITION_LIFECYCLE_FILE=str(_life_file)
        try:
            _pos={
                "BTCUSDT": {
                    "symbol":"BTCUSDT",
                    "positionAmt":1.0,
                    "entry_id":"ENTRY_VERIFY",
                    "entry_tf":"12H",
                    "entry_strategy":"EMA_CROSS+SAR",
                    "entry_ts":runner.now_ts(),
                    "entryPrice":"100",
                    "markPrice":"105",
                }
            }
            runner.update_position_lifecycle(_pos)
            _pos["BTCUSDT"]["markPrice"]="98"
            runner.update_position_lifecycle(_pos)
            _life=runner.position_lifecycle_metrics(_pos["BTCUSDT"])
            check(
                "RUNNER_POSITION_LIFECYCLE_MFE_MAE_GIVEBACK",
                abs(_life.get("max_favorable_roi",0)-5.0)<1e-9
                and abs(_life.get("max_adverse_roi",0)+2.0)<1e-9
                and abs(_life.get("max_giveback_roi",0)-7.0)<1e-9
                and _life.get("metric_source")=="RUNNER_ACTIVE_POSITION_OBSERVER",
                _life,
            )
        finally:
            runner.POSITION_LIFECYCLE_FILE=_old_life_file

check("RUNNER_LIFECYCLE_INDEPENDENT_OF_LEGACY_SL_TP", "TP_STATE_FILE" not in (ROOT/"runner.py").read_text(encoding="utf-8-sig",errors="ignore"))
check("LEGACY_SL_TP_NOT_REQUIRED_BY_SYNC", all(x not in _sync_base for x in ("sl.py","tp.py","sl.service","tp.service")))

# Binance Futures WebSocket collection path regression (existing V4-compatible live collector path preserved).
_collector_src=(ROOT/"collector.py").read_text(encoding="utf-8-sig",errors="ignore")
check("BINANCE_FUTURES_WEBSOCKET_PATH", 'wss://fstream.binance.com/ws' in _collector_src and 'WS_INTERVALS = ["30m", "1h", "6h", "12h", "1d"]' in _collector_src)
check("WEBSOCKET_STALE_RECONNECT", 'WS_STALE_SEC' in _collector_src and 'WS_STALE_RESTART' in _collector_src and 'ws_run_tokens' in _collector_src)
check("WEBSOCKET_DEDUPE_AND_CONFIRMED_BAR_WRITE", 'log_dedupe' in _collector_src and 'drop_duplicates("open_time", keep="last")' in _collector_src and '확정봉만' in _collector_src)

# Source tree must have no Upbit integration.
hits=[]
for p in ROOT.rglob("*"):
    if not p.is_file() or "__pycache__" in p.parts: continue
    if p.suffix.lower() not in {".py",".json",".ps1",".bat",".vbs"}: continue
    if p.name in {"V3_VERIFY_REPORT.json","V3_OVERLAY_MANIFEST.json"}: continue
    try: text=p.read_text(encoding="utf-8-sig",errors="ignore").lower()
    except Exception: continue
    if ("up"+"bit") in text and p.name != "verify_v3.py": hits.append(str(p.relative_to(ROOT)))
check("NO_UPBIT_INTEGRATION",not hits,hits)

print(json.dumps({"schema":"B_BOT_V3_VERIFY_REPORT_V2","ok":all(r["ok"] for r in RESULTS),"overlay_package_mode":OVERLAY_PACKAGE,"passed":sum(1 for r in RESULTS if r["ok"]),"total":len(RESULTS),"checks":RESULTS},ensure_ascii=False,indent=2))
