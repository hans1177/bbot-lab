# -*- coding: utf-8 -*-
# 파일명: strategy_lab/research_controller.py
"""B-Bot V3 거버넌스형 bounded 연구 Controller.

Roadmap Constitution 안에서만 연구하며 Stable Trading Kernel은 자동 수정하지 않는다.
실제 전략/정책 교체는 검증된 Version + Strategy Runtime Sync를 통해서만 진행한다.
"""
from __future__ import annotations

# ============================================================
# 임포트
# ============================================================

import argparse
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

try:
    import msvcrt
except Exception:
    msvcrt = None

try:
    import fcntl
except ImportError:
    fcntl = None

# ============================================================
# 경로 / 모듈
# ============================================================

ROOT = Path(os.getenv("BBOT_LOCAL_ROOT", r"C:\my\bottest"))
LAB = ROOT / "strategy_lab"
RES = LAB / "results"
V3 = RES / "v3"
LOCK = V3 / "research_controller.lock"
PENDING = V3 / "runtime_sync_request.json"
PUSH_HOLD = ROOT / "SERVER_PUSH_HOLD.flag"

sys.path.insert(0, str(LAB))

import v3_baseline_research as baseline_research
import v3_challenger_research as challenger_research
import v3_combination_research as combination_research
import v3_deployment as deploy
import v3_evidence_research as evidence_research
import v3_governance as governance
import v3_hypothesis_factory as hypothesis_factory
import v3_policy_research as policy_research
import v3_research_knowledge as knowledge
import v3_strategy_research as strategy_research
import v3_task_queue as task_queue

# ============================================================
# 상수
# ============================================================

MAIN_FILL_TF_ORDER = ["30M", "1H", "6H", "12H", "1D", "1W"]
RESEARCH_TF_ORDER = ["6H", "1H", "1D", "1W", "30M", "12H"]
DEPLOY_TF_ORDER = ["30M", "1H", "6H", "12H", "1D", "1W"]

# ============================================================
# 유틸 / 락
# ============================================================


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return default


@contextmanager
def lock():
    """기존 연구 잠금을 Windows와 Linux에서 동일하게 배타적으로 획득한다."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    f = LOCK.open("a+b")
    acquired = False
    try:
        if os.name == "nt":
            if msvcrt is None:
                raise RuntimeError("RESEARCH_LOCK_BACKEND_UNAVAILABLE")
            f.seek(0)
            f.write(b"0")
            f.flush()
            f.seek(0)
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError("RESEARCH_ALREADY_RUNNING") from error
        else:
            if fcntl is None:
                raise RuntimeError("RESEARCH_LOCK_BACKEND_UNAVAILABLE")
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("RESEARCH_ALREADY_RUNNING") from error
        acquired = True
        yield
    finally:
        try:
            if acquired:
                if os.name == "nt":
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        finally:
            f.close()


def config() -> dict:
    return load(ROOT / "strategy_config.json", {})


def research_architecture() -> dict:
    arch = (
        governance.constitution()
        .get("research_architecture_v3")
        or {}
    )

    if arch.get("policy_id") != (
        "RA_MAIN_MIDDLE_DETAIL_20260830_V1"
    ):
        raise RuntimeError(
            "RESEARCH_ARCHITECTURE_INVALID"
        )

    return arch


def research_scheduler() -> dict:
    scheduler = (
        research_architecture()
        .get("research_scheduler")
        or {}
    )

    if scheduler.get("status") != (
        "FINAL_CONFIRMED"
    ):
        raise RuntimeError(
            "RESEARCH_SCHEDULER_INVALID"
        )

    return scheduler


def frame_live(tf: str) -> bool:
    row = ((config().get("frames") or {}).get(tf) or {})
    return bool(row.get("enabled")) and str(row.get("deployment_tier") or "OFF").upper() in {"CANARY", "SMALL", "NORMAL"}


def can_accept_canary_peer(tf: str) -> bool:
    tf = str(tf or "").upper()

    s = deploy.state() or {}

    count = 0

    for row in (
        s.get("strategy_instances")
        or {}
    ).values():

        if not isinstance(row, dict):
            continue

        if str(
            row.get("timeframe")
            or ""
        ).upper() != tf:
            continue

        tier = str(
            row.get("tier")
            or ""
        ).upper()

        qualification = str(
            row.get("qualification_status")
            or ""
        ).upper()

        if (
            tier in {
                "PENDING_SHADOW",
                "LOCAL_PROVISIONAL_READY",
                "PENDING_CANARY_SYNC",
                "CANARY",
            }
            or qualification == "CANARY"
        ):
            count += 1

    return count < 8


def candidate_already_tracked(
    tf: str,
    row: dict,
) -> bool:

    tf = str(tf or "").upper()

    strategy = str(
        (row or {}).get("strategy")
        or ""
    ).upper()

    if not strategy:
        return True

    s = deploy.state() or {}

    for inst in (
        s.get("strategy_instances")
        or {}
    ).values():

        if not isinstance(inst, dict):
            continue

        if str(
            inst.get("timeframe")
            or ""
        ).upper() != tf:
            continue

        if str(
            inst.get("strategy")
            or inst.get("strategy_id")
            or ""
        ).upper() != strategy:
            continue

        tier = str(
            inst.get("tier")
            or ""
        ).upper()

        if tier not in {
            "",
            "ROLLED_BACK",
            "REJECTED",
            "FAILED",
        }:
            return True

    return False


MAIN_FILL_OCCUPIED_TIERS = {
    "LOCAL_PROVISIONAL_READY",
    "PENDING_CANARY_SYNC",
    "CANARY",
    "SMALL",
    "NORMAL",
}


def main_fill_occupied(tf: str) -> bool:
    row=((deploy.state().get("frames") or {}).get(tf) or {})
    strategy=str(row.get("strategy") or "").strip()
    tier=str(
        row.get("tier")
        or row.get("deployment_tier")
        or ""
    ).upper()

    if strategy and tier in MAIN_FILL_OCCUPIED_TIERS:
        return True

    return frame_live(tf)


def empty_main_fill_tfs():
    # Dynamic deployment state only; no fixed winning timeframe.
    #
    # Existing deployed CANARY/SMALL/NORMAL instances remain untouched
    # for runtime/position management.  Research fill priority, however,
    # must also include frames whose currently deployed exact version has
    # already failed/superseded in the current validation pipeline.
    pipeline_path = (
        ROOT
        / "strategy_lab"
        / "results"
        / "v3"
        / "historical_314_pipeline_stage2.json"
    )

    pipeline = load(pipeline_path, {})
    entries = pipeline.get("entries") or []

    research_refill_stages = {
        "REDEVELOPMENT_SUPERSEDED_BY_NEW_VERSION",
        "HARD_PASS_RECHECK_FAIL",
        "HARD_PASS_FAIL",
    }

    out = []

    state = deploy.state() or {}
    frames = state.get("frames") or {}

    for tf in MAIN_FILL_TF_ORDER:
        if not main_fill_occupied(tf):
            out.append(tf)
            continue

        frame = frames.get(tf) or {}
        deployed_exact = str(
            frame.get("strategy_version")
            or ""
        ).strip()

        if not deployed_exact:
            continue

        matched = [
            row
            for row in entries
            if isinstance(row, dict)
            and str(row.get("timeframe") or "").upper() == tf
            and str(row.get("exact_strategy_version") or "").strip()
                == deployed_exact
        ]

        if len(matched) != 1:
            continue

        stage = str(
            matched[0].get("pipeline_stage")
            or ""
        ).upper()

        if stage in research_refill_stages:
            out.append(tf)

    return out




def _record_strategy_track(event: str, result: dict) -> None:
    task = {
        "task_id": f"STRATEGY_{event}_{int(time.time())}",
        "track": "STRATEGY",
        "input_signature": task_queue.input_signature("STRATEGY"),
        "contract": task_queue.task_contract("STRATEGY"),
    }

    status = str(
        result.get("status")
        or "MEASURED"
    ).upper()

    mapped = (
        status
        if status in {
            "PASS",
            "FAIL",
            "MEASURED",
            "CANDIDATE",
            "VALIDATED",
        }
        else "MEASURED"
    )

    task_queue.record(
        task,
        {
            "status": mapped,
            "event": event,
            "summary": result,
        },
    )

    # Strategy Passport only when exact version exists.
    version_id = str(
        result.get("strategy_version")
        or result.get("version_id")
        or ""
    ).strip()

    if not version_id.startswith("SV_"):
        return

    timeframe = str(
        result.get("timeframe")
        or result.get("tf")
        or ""
    ).upper()

    strategy = (
        result.get("strategy")
        or result.get("strategy_name")
    )

    governance.ensure_strategy_passport(
        version_id,
        {
            "timeframe": timeframe,
            "strategy": strategy,
            "source": "research_controller",
        },
    )

    governance.record_strategy_career_event(
        version_id,
        "RESEARCH",
        {
            "source_event": event,
            "research_status": mapped,
            "timeframe": timeframe,
            "strategy": strategy,
            "result_json": result.get("result_json"),
            "checkpoint": result.get("checkpoint"),
        },
        event_id=task["task_id"] + ":" + version_id,
    )


# ============================================================
# 상태
# ============================================================


def controller_status() -> dict:
    cfg = config()
    ds = deploy.state()
    rs = strategy_research.status()
    cv = governance.validate_constitution()

    arch = research_architecture()
    scheduler = research_scheduler()

    return {
        "schema":
            "B_BOT_V3_CONTROLLER_STATUS_V3",

        "updated_at":
            now(),

        "pending_runtime_sync":
            PENDING.exists(),

        "server_push_hold":
            PUSH_HOLD.exists(),

        "constitution":
            cv,

        "research_architecture_v3": {
            "policy_id":
                arch.get("policy_id"),
            "status":
                arch.get("status"),
            "MAIN":
                (arch.get("MAIN") or {})
                .get("stages"),
            "MIDDLE":
                (arch.get("MIDDLE") or {})
                .get("stages"),
            "DETAIL":
                (arch.get("DETAIL") or {})
                .get("stages"),
        },

        "research_scheduler": {
            "schema":
                scheduler.get("schema"),
            "status":
                scheduler.get("status"),
            "resource_allocation":
                task_queue.resource_allocation(),
        },

        "roadmap_queue":
            task_queue.status(),

        "frames": {
            tf: {
                "runtime":
                    (cfg.get("frames") or {})
                    .get(tf),

                "deployment":
                    (ds.get("frames") or {})
                    .get(tf),

                "research":
                    (rs.get("timeframes") or {})
                    .get(tf),
            }

            for tf in RESEARCH_TF_ORDER
        },

        "trial73":
            "COMPLETED_DO_NOT_RERUN",

        "legacy_trial_counters": {
            "search":
                73,
            "parameter":
                73,
        },

        "v3_trial_counter":
            knowledge.ensure()[1]
            .get("counter", 0),

        "version_registry":
            governance.version_registry(),
    }



# ============================================================
# Bounded Controller 1회
# ============================================================




def _new_research_available():
    if strategy_research.choose_next(): return True
    if hypothesis_factory.choose_next(): return True
    for tf in strategy_research.TF_ORDER:
        try:
            if combination_research.hypotheses(tf): return True
        except Exception:
            continue
    search = config().get("candidate_search") or {}
    if search.get("enabled") is True:
        contract = search.get("contract") or {}
        tf = str(contract.get("timeframe") or "").upper()
        knowledge_state, _ = knowledge.ensure()
        checkpoint = (knowledge_state.get("records") or {}).get("candidate_search_wave:" + tf) or {}
        if checkpoint.get("run_id") != contract.get("run_id"):
            return True
        import v3_memory_hypothesis_wave as candidate_wave
        manifest = candidate_wave.wave(tf, search_contract=contract)
        done = hypothesis_factory.completed_pairs()
        return any((str(row.get("name") or "").upper(), tf) not in done for row in manifest.get("candidates") or [])
    return False


def _run_new_research_one(held):
    nxt=strategy_research.choose_next()
    if nxt:
        row=strategy_research.run_one(); _record_strategy_track("AUTO_MANAGER_SINGLE_FIXED_WINDOW",row); return {"status":"DONE","action":"SINGLE_RESEARCH","result":row}
    nxt=hypothesis_factory.choose_next()
    if nxt:
        row=hypothesis_factory.run_one(); _record_strategy_track("AUTO_MANAGER_SAFE_GRAMMAR_FIXED_WINDOW",row); return {"status":"DONE","action":"SAFE_GRAMMAR_RESEARCH","result":row}
    try:
        tf=combination_research.choose_next_tf()
    except Exception:
        tf=None
    if tf:
        row=combination_research.run_one(tf); _record_strategy_track("AUTO_MANAGER_COMBINATION_FIXED_WINDOW",row if isinstance(row,dict) else {"status":"MEASURED"}); return {"status":"DONE","action":"COMBINATION_RESEARCH","timeframe":tf,"result":row}
    search = config().get("candidate_search") or {}
    if search.get("enabled") is True:
        import v3_memory_hypothesis_wave as candidate_wave
        contract = search.get("contract") or {}
        tf = str(contract.get("timeframe") or "").upper()
        manifest = candidate_wave.wave(tf, search_contract=contract)
        done = hypothesis_factory.completed_pairs()
        pending = [row for row in manifest["candidates"] if (str(row["name"]).upper(), tf) not in done]
        if pending:
            row = hypothesis_factory.run_one(candidate=pending[0])
            _record_strategy_track("AUTO_MANAGER_GENETIC_OPTUNA_FIXED_WINDOW", row)
            return {"status": "DONE", "action": "SEALED_SEARCH_CANDIDATE_RESEARCH", "result": row,
                    "wave_id": manifest["wave_id"], "automatic_promotion": False}
    return {"status":"NO_NEW_RESEARCH_BACKLOG"}


def _managed_availability(work):
    new_avail=_new_research_available(); redev=work.get("redevelopment_pending",0)>0
    if work.get("historical_backlog_mode"):
        return {
            "HISTORICAL_314_PIPELINE": bool(work.get("hard_pass_pending") or work.get("reentry_pending") or work.get("final_risk_pending")),
            "INDIVIDUAL_TRAILING": bool(work.get("trailing_pending")),
            "NEW_PURE_AND_HARD_PASS": new_avail,
            "TARGETED_REDEVELOPMENT": redev,
            "FULL_VALIDATION_AND_BLIND": bool(work.get("full_validation_pending") or work.get("blind_prepare_pending") or work.get("blind_evaluation_pending")),
        }
    return {
        "NEW_PURE_AND_HARD_PASS":new_avail,
        "ENTRY_AND_FINAL_RISK":bool(work.get("hard_pass_pending") or work.get("final_risk_pending")),
        "TRAILING_AND_REENTRY":bool(work.get("trailing_pending") or work.get("reentry_pending")),
        "FULL_VALIDATION":bool(work.get("full_validation_pending")),
        "TARGETED_REDEVELOPMENT":redev,
        "SEALED_FINAL_BLIND":bool(work.get("blind_prepare_pending") or work.get("blind_evaluation_pending")),
    }


def _dispatch_managed_bucket(bucket, work, held):
    # ?? ??? ?? ??? ???? ??? ??? ??? ?? ????.
    if bucket == "NEW_PURE_AND_HARD_PASS":
        return _run_new_research_one(held)

    if bucket in {"HISTORICAL_314_PIPELINE", "ENTRY_AND_FINAL_RISK"}:
        if work.get("final_risk_pending"):
            return strategy_research.run_final_risk_one()
        if bucket == "HISTORICAL_314_PIPELINE" and work.get("reentry_pending"):
            return strategy_research.run_individual_reentry_one()
        if work.get("hard_pass_pending"):
            return strategy_research.run_historical_reentry_one()

    if bucket in {"INDIVIDUAL_TRAILING", "TRAILING_AND_REENTRY"}:
        if work.get("reentry_pending"):
            return strategy_research.run_individual_reentry_one()
        if work.get("trailing_pending"):
            return strategy_research.run_individual_trailing_one()

    if bucket == "TARGETED_REDEVELOPMENT":
        return strategy_research.run_targeted_redevelopment_one()

    if bucket in {"FULL_VALIDATION", "FULL_VALIDATION_AND_BLIND"} and work.get("full_validation_pending"):
        return strategy_research.run_full_validation_one()

    if bucket in {"SEALED_FINAL_BLIND", "FULL_VALIDATION_AND_BLIND"}:
        if work.get("blind_prepare_pending"):
            return strategy_research.prepare_sealed_final_blind_one()
        if work.get("blind_evaluation_pending"):
            return strategy_research.run_sealed_final_blind_one()

    return {"status": "NO_WORK_FOR_SELECTED_BUCKET", "bucket": bucket}


def managed_research_once(held: bool) -> dict:
    if not held: return {"action":"GOVERNANCE_BLOCK","reason":"AUTO_RESEARCH_SERVER_PUSH_HOLD_REQUIRED"}
    sync=strategy_research.sync_new_pipeline_entries(); work=strategy_research.pipeline_work_status(); availability=_managed_availability(work)
    selection=task_queue.preview_auto_research_bucket(availability,bool(work.get("historical_backlog_mode")))
    if selection is None:
        task=task_queue.select_next(exclude={"STRATEGY"})
        if task:
            started=time.perf_counter(); row=policy_research.run_task(task); elapsed=time.perf_counter()-started
            return {"action":"LEGACY_POLICY_MAINTENANCE_NO_UNIFIED_BACKLOG","server_push_hold":held,"track":task.get("track"),"task":task,"result":row,"elapsed_seconds":elapsed,"pipeline_sync":sync,"pipeline_work":work}
        return {"action":"NO_NEW_BOUNDED_INPUT","server_push_hold":held,"pipeline_sync":sync,"pipeline_work":work,"manager":task_queue.auto_research_manager_status()}
    started=time.perf_counter(); row=_dispatch_managed_bucket(selection["bucket"],work,held); elapsed=time.perf_counter()-started
    task_queue.record_auto_research_bucket(selection,elapsed,row if isinstance(row,dict) else {"status":"UNKNOWN"})
    return {"action":"AUTO_RESEARCH_MANAGER_STAGE10","server_push_hold":held,"selection":selection,"elapsed_seconds":elapsed,"pipeline_sync":sync,"pipeline_work":work,"result":row,"automatic_promotion":False}


def managed_dry_run(include_inputs: bool = False, export_inputs=None) -> dict:
    cv=governance.validate_constitution(); work=strategy_research.pipeline_work_status();
    samples={
        "backlog":task_queue.preview_auto_research_bucket({"HISTORICAL_314_PIPELINE":True,"INDIVIDUAL_TRAILING":True,"NEW_PURE_AND_HARD_PASS":True,"TARGETED_REDEVELOPMENT":True,"FULL_VALIDATION_AND_BLIND":True},True,{}),
        "normal":task_queue.preview_auto_research_bucket({"NEW_PURE_AND_HARD_PASS":True,"ENTRY_AND_FINAL_RISK":True,"TRAILING_AND_REENTRY":True,"FULL_VALIDATION":True,"TARGETED_REDEVELOPMENT":True,"SEALED_FINAL_BLIND":True},False,{}),
    }
    result = {"ok":bool(cv.get("ok")) and bool(samples["backlog"]) and bool(samples["normal"]),"constitution":cv,"pipeline_work":work,"scheduler_preview":samples,"manager_status":task_queue.auto_research_manager_status(),"mutated":False,"orders_touched":False}
    # 연구 이전 입력 검사: 상태·시세·전략 소스는 읽기만 한다.
    if include_inputs:
        import hashlib
        required = (
            'gate_lab_fast.py', 'gate_lab_exact_long_cache.parquet',
            'ROADMAP_CONSTITUTION_V3.json', 'strategy_lab/lab_config.json',
            'strategy_lab/results/v3/historical_314_pipeline_stage2.json',
            'strategy_lab/results/v3/trial_ledger.json',
            'strategy_lab/results/v3/research_knowledge.json',
        )
        files = {}
        for name in required:
            path = ROOT / name
            exists = path.is_file()
            files[name] = {'exists': exists, 'bytes': path.stat().st_size if exists else 0}
        candles = [p for p in (ROOT / 'data' / 'candles').rglob('*.parquet') if p.is_file()]
        candle_bytes = sum(p.stat().st_size for p in candles)
        source_errors = []
        source_count = 0
        windows_paths = 0
        terminal = {'REDEVELOPMENT_SUPERSEDED_BY_NEW_VERSION', 'HARD_PASS_RECHECK_FAIL', 'PERSISTENT_FAIL'}
        pipeline = load(ROOT / required[4], {})
        entries = pipeline.get('entries') if isinstance(pipeline, dict) else None
        ledger = load(ROOT / required[5], {})
        memory = load(ROOT / required[6], {})
        state_valid = (
            isinstance(entries, list) and bool(entries)
            and isinstance(ledger, dict) and ledger.get('schema') == 'B_BOT_V3_TRIAL_LEDGER_V1' and isinstance(ledger.get('entries'), list)
            and isinstance(memory, dict) and isinstance(memory.get('records'), dict)
        )
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or entry.get('eligible') is not True or entry.get('pipeline_stage') in terminal:
                continue
            relative = str(entry.get('canonical_source_file') or '')
            source_count += 1
            windows_paths += int('\\' in relative)
            path = (ROOT / relative.replace('\\', '/')).resolve()
            expected = str(entry.get('source_sha256') or '').lower()
            reason = None
            if not relative or not path.is_relative_to(ROOT.resolve()):
                reason = 'SOURCE_PATH_OUTSIDE_RESEARCH_ROOT'
            elif not path.is_file():
                reason = 'CANONICAL_SOURCE_FILE_MISSING'
            elif not expected or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                reason = 'SOURCE_SHA256_MISMATCH'
            if reason:
                source_errors.append({'strategy_id': entry.get('strategy_id'), 'exact_strategy_version': entry.get('exact_strategy_version'), 'reason': reason})
        missing = [name for name, row in files.items() if not row['exists'] or not row['bytes']]
        inventory = {
            'files': files, 'missing': missing,
            'candles': {'files': len(candles), 'bytes': candle_bytes},
            'checkpoint_structure_valid': state_valid,
            'active_sources_checked': source_count,
            'source_error_count': len(source_errors), 'source_errors': source_errors[:20],
            'windows_source_path_count': windows_paths,
            'portable_path_review_required': bool(windows_paths),
            'metadata_only': True, 'official_market_data_validation': False,
        }
        inventory['input_files_present'] = bool(not missing and candles and candle_bytes and state_valid and not source_errors)
        result['input_inventory'] = inventory
        result['ok'] = bool(result['ok'] and inventory['input_files_present'])
    # 최신 입력 인계: 공개 업로드 없이 로컬 압축만 생성한다.
    if export_inputs is not None:
        import re
        import tempfile
        import zipfile
        import hashlib
        destination = Path(export_inputs).resolve()
        if not include_inputs or not result['ok']:
            raise RuntimeError('EXPORT_INPUT_CHECK_REQUIRED')
        if destination.suffix.lower() != '.zip' or destination.is_relative_to(ROOT.resolve()):
            raise ValueError('EXPORT_OUTSIDE_RESEARCH_ROOT_ZIP_REQUIRED')
        split_destination = destination.with_suffix('')
        if destination.exists() or split_destination.exists():
            raise FileExistsError('EXPORT_DESTINATION_ALREADY_EXISTS')
        if not destination.parent.is_dir():
            raise FileNotFoundError('EXPORT_PARENT_MISSING')
        paths = {ROOT / name for name in required}
        candle_paths = set(candles)
        paths.update(candle_paths)
        paths.update(p for p in LAB.rglob('*') if p.is_file() and p.suffix.lower() in {'.py', '.json', '.jsonl', '.csv', '.parquet'} and not any(part in {'__pycache__', '.git', '.venv', 'server_runtime', 'logs'} for part in p.relative_to(LAB).parts) and p.name != 'server_exchange_snapshot.json')
        total = sum(p.stat().st_size for p in paths)
        if total > 4 * 1024**3:
            raise RuntimeError('EXPORT_INPUTS_EXCEED_4_GIB')
        import shutil
        # 압축 전에 짧은 일관성 구간으로 입력 사본을 확정한다. 긴 압축은 사본만 읽는다.
        # 기존 연구 잠금은 유지하며 원본 변경 검사를 생략하지 않는다.
        if shutil.disk_usage(destination.parent).free < 2 * total + 64 * 1024**2:
            raise RuntimeError('EXPORT_SNAPSHOT_DISK_SPACE_REQUIRED')
        with tempfile.TemporaryDirectory(prefix='.research-snapshot-', dir=str(destination.parent)) as snapshot_dir:
            snapshot_started_at = now()
            snapshot_paths = {}
            source_fingerprints = {}
            for path in sorted(paths):
                if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):
                    raise RuntimeError('EXPORT_PATH_OUTSIDE_RESEARCH_ROOT')
                before = path.stat()
                if before.st_size > 512 * 1024**2:
                    raise RuntimeError('EXPORT_SINGLE_FILE_EXCEEDS_512_MIB')
                source_fingerprints[path] = (before.st_size, before.st_mtime_ns, before.st_ino)
            for index, path in enumerate(sorted(paths)):
                raw = path.read_bytes()
                after = path.stat()
                signature = (after.st_size, after.st_mtime_ns, after.st_ino)
                if signature != source_fingerprints[path] or len(raw) != after.st_size:
                    raise RuntimeError('EXPORT_INPUT_CHANGED_RETRY:' + path.relative_to(ROOT).as_posix())
                frozen = Path(snapshot_dir) / str(index)
                frozen.write_bytes(raw)
                snapshot_paths[path] = frozen
            for path, signature in source_fingerprints.items():
                if path in candle_paths:
                    continue
                current = path.stat()
                if signature != (current.st_size, current.st_mtime_ns, current.st_ino):
                    raise RuntimeError('EXPORT_INPUT_CHANGED_RETRY:' + path.relative_to(ROOT).as_posix())
            # 연구 상태와 해당 버전 소스의 연결도 확정된 사본끼리 재검증한다.
            snapshot_pipeline = json.loads(snapshot_paths[ROOT / required[4]].read_bytes().decode('utf-8-sig'))
            snapshot_ledger = json.loads(snapshot_paths[ROOT / required[5]].read_bytes().decode('utf-8-sig'))
            snapshot_memory = json.loads(snapshot_paths[ROOT / required[6]].read_bytes().decode('utf-8-sig'))
            if not (
                isinstance(snapshot_pipeline, dict) and isinstance(snapshot_pipeline.get('entries'), list) and snapshot_pipeline['entries']
                and isinstance(snapshot_ledger, dict) and snapshot_ledger.get('schema') == 'B_BOT_V3_TRIAL_LEDGER_V1' and isinstance(snapshot_ledger.get('entries'), list)
                and isinstance(snapshot_memory, dict) and isinstance(snapshot_memory.get('records'), dict)
            ):
                raise RuntimeError('EXPORT_SNAPSHOT_CHECKPOINT_INVALID')
            for entry in snapshot_pipeline['entries']:
                if not isinstance(entry, dict) or entry.get('eligible') is not True or entry.get('pipeline_stage') in terminal:
                    continue
                relative = str(entry.get('canonical_source_file') or '')
                canonical = (ROOT / relative.replace('\\', '/')).resolve()
                expected = str(entry.get('source_sha256') or '').lower()
                frozen = snapshot_paths.get(canonical)
                if not relative or not expected or frozen is None or hashlib.sha256(frozen.read_bytes()).hexdigest() != expected:
                    raise RuntimeError('EXPORT_SNAPSHOT_CANONICAL_SOURCE_MISMATCH')
            snapshot_completed_at = now()
            patterns = (
                rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
                rb'(?:AKIA|ASIA)[A-Z0-9]{16}',
                rb'gh[pousr]_[A-Za-z0-9]{30,}',
                rb'\d{9,12}:[A-Za-z0-9_-]{35}',
            )
            # 압축 전에 검사하고 압축 시 동일 바이트인지 해시로 다시 묶는다.
            scanned_hashes = {}
            feedback_path = LAB / 'results' / 'v3' / 'runtime_feedback.json'
            for path in sorted(paths, key=lambda p: (p != feedback_path, str(p))):
                if path.suffix.lower() not in {'.py', '.json', '.jsonl', '.csv'}:
                    continue
                if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):
                    raise RuntimeError('EXPORT_PATH_OUTSIDE_RESEARCH_ROOT')
                if path.stat().st_size > 512 * 1024**2:
                    raise RuntimeError('EXPORT_SINGLE_FILE_EXCEEDS_512_MIB')
                raw = snapshot_paths[path].read_bytes()
                relative = path.relative_to(ROOT).as_posix()
                scan_raw = raw
                # 원장 생성식의 EXTERNAL_TRADE 이벤트와 해당 기록의 종목·LEG를 대조한다.
                # 압축에 저장하는 원본 바이트와 연구 증거는 변경하지 않는다.
                if relative == 'strategy_lab/results/v3/runtime_feedback.json':
                    feedback = json.loads(raw.decode('utf-8-sig'))
                    if isinstance(feedback, dict) and feedback.get('schema') == 'B_BOT_R4_RUNTIME_RESEARCH_FEEDBACK_V1':
                        for record in feedback.get('records') or []:
                            if not isinstance(record, dict) or not isinstance(record.get('exit_fill_ids'), list):
                                continue
                            for index, value in enumerate(record['exit_fill_ids']):
                                event = re.fullmatch(r'EXTERNAL_TRADE:([A-Z0-9]{2,32}):[0-9]{1,20}:(LEG_[0-9a-f]{32})', value) if isinstance(value, str) else None
                                if event and event.group(1) == record.get('symbol') and event.group(2) == record.get('leg_id'):
                                    record['exit_fill_ids'][index] = 'VERIFIED_R4_FILL_EVENT_ID'
                        scan_raw = json.dumps(feedback, ensure_ascii=False).encode('utf-8')
                if path.suffix.lower() in {'.py', '.json', '.jsonl', '.csv'}:
                    for pattern_index, pattern in enumerate(patterns):
                        match = re.search(pattern, scan_raw)
                        if match:
                            # 파일 경로와 종류만 반환하고 탐지된 값은 노출하지 않는다.
                            raise RuntimeError('EXPORT_CREDENTIAL_PATTERN_DETECTED:' + relative + ':RULE_' + str(pattern_index + 1))
                scanned_hashes[path] = hashlib.sha256(raw).hexdigest()
            fd, temporary = tempfile.mkstemp(prefix='.research-export-' + destination.stem + '-', suffix='.tmp', dir=str(destination.parent))
            os.close(fd)
            manifest = {'schema': 'B_BOT_RESEARCH_INPUT_HANDOFF_V1', 'created_at': now(),
                        'snapshot_started_at': snapshot_started_at, 'snapshot_completed_at': snapshot_completed_at,
                        'public_upload_allowed': False, 'automatic_promotion': False,
                        'orders_touched': False, 'files': {}}
            fingerprints = {}
            split_stage = None
            try:
                with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
                    for path in sorted(paths):
                        resolved = path.resolve()
                        if path.is_symlink() or not resolved.is_relative_to(ROOT.resolve()):
                            raise RuntimeError('EXPORT_PATH_OUTSIDE_RESEARCH_ROOT')
                        before = snapshot_paths[path].stat()
                        if before.st_size > 512 * 1024**2:
                            raise RuntimeError('EXPORT_SINGLE_FILE_EXCEEDS_512_MIB')
                        raw = snapshot_paths[path].read_bytes()
                        after = snapshot_paths[path].stat()
                        signature = (before.st_size, before.st_mtime_ns, before.st_ino)
                        if signature != (after.st_size, after.st_mtime_ns, after.st_ino) or len(raw) != before.st_size:
                            raise RuntimeError('EXPORT_SNAPSHOT_CHANGED:' + path.relative_to(ROOT).as_posix())
                        relative = path.relative_to(ROOT).as_posix()
                        if path in scanned_hashes and hashlib.sha256(raw).hexdigest() != scanned_hashes[path]:
                            raise RuntimeError('EXPORT_SNAPSHOT_CHANGED:' + path.relative_to(ROOT).as_posix())
                        archive.writestr(relative, raw)
                        manifest['files'][relative] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
                        fingerprints[path] = signature
                        # 시세는 수집기가 별도로 갱신한다. 각 파일의 읽기 전후 상태와
                        # 압축에 담은 실제 바이트 해시를 보존한다.
                    for path, signature in fingerprints.items():
                        if path in candle_paths:
                            continue
                        current = snapshot_paths[path].stat()
                        if signature != (current.st_size, current.st_mtime_ns, current.st_ino):
                            raise RuntimeError('EXPORT_SNAPSHOT_CHANGED:' + path.relative_to(ROOT).as_posix())
                    archive.writestr('HANDOFF.json', json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
                size = Path(temporary).stat().st_size
                # 전체 ZIP 바이트를 나누므로 연구 파일이나 기록을 생략하지 않는다.
                part_limit = int(os.getenv('BBOT_EXPORT_PART_BYTES') or 200 * 1024**2)
                if not 1024**2 <= part_limit <= 200 * 1024**2:
                    raise ValueError('EXPORT_PART_LIMIT_INVALID')
                outputs = []
                if size <= part_limit:
                    os.link(temporary, destination)
                    outputs = [str(destination)]
                    final_path = destination
                else:
                    import shutil
                    split_stage = Path(tempfile.mkdtemp(prefix='.research-parts-', dir=str(destination.parent)))
                    transfer = {'schema': 'B_BOT_RESEARCH_TRANSFER_PARTS_V1',
                                'archive_name': destination.name, 'archive_bytes': size,
                                'public_upload_allowed': False, 'parts': []}
                    archive_hash = hashlib.sha256()
                    with Path(temporary).open('rb') as stream:
                        index = 1
                        while True:
                            block = stream.read(part_limit)
                            if not block:
                                break
                            name = destination.name + '.' + str(index).zfill(3)
                            (split_stage / name).write_bytes(block)
                            archive_hash.update(block)
                            transfer['parts'].append({'name': name, 'bytes': len(block), 'sha256': hashlib.sha256(block).hexdigest()})
                            index += 1
                    transfer['archive_sha256'] = archive_hash.hexdigest()
                    (split_stage / 'TRANSFER.json').write_text(json.dumps(transfer, indent=2, sort_keys=True), encoding='utf-8')
                    # 연구 폴더 밖의 새 디렉터리로만 게시한다. 기존 파일은 보존한다.
                    if split_destination.exists():
                        raise FileExistsError('EXPORT_DESTINATION_ALREADY_EXISTS')
                    split_stage.rename(split_destination)
                    split_stage = None
                    final_path = split_destination
                    outputs = [str(split_destination / row['name']) for row in transfer['parts']]
                    outputs.append(str(split_destination / 'TRANSFER.json'))
                result['export'] = {'path': str(final_path), 'bytes': size, 'files': len(manifest['files']),
                                    'created': True, 'outputs': outputs, 'public_upload_allowed': False,
                                    'source_files_modified': False, 'server_write': False,
                                    'snapshot_started_at': snapshot_started_at, 'snapshot_completed_at': snapshot_completed_at}
            finally:
                Path(temporary).unlink(missing_ok=True)
                if split_stage is not None:
                    import shutil
                    shutil.rmtree(split_stage)

    return result


def run_once(enable_research: bool = True) -> dict:
    constitution = governance.validate_constitution()
    if not constitution.get("ok"):
        return {"action": "GOVERNANCE_BLOCK", "reason": "ROADMAP_CONSTITUTION_INVALID", "detail": constitution}

    # Bind every controller cycle to the final
    # MAIN/MIDDLE/DETAIL Constitution + Research Scheduler.
    architecture = research_architecture()
    scheduler_contract = research_scheduler()

    held = PUSH_HOLD.exists() or str(os.getenv("B_BOT_V3_SERVER_PUSH_HOLD") or "").strip()=="1"
    kernel_before = governance.capture_kernel_guard()
    result = None

    if str(os.getenv("BBOT_AUTO_RESEARCH_MANAGER_V1") or "").strip()=="1" and enable_research:
        result=managed_research_once(held)
        governance.assert_kernel_unchanged(kernel_before)
        return result

    # 1) Runtime 전환은 HOLD 해제 뒤에만 materialize/scale/rollback한다.
    if not held and not PENDING.exists():
        ready = deploy.materialize_local_ready_request()
        if ready:
            result = {"action": "RUNTIME_SYNC_REQUEST_FROM_LOCAL_READY", "request": ready}
        if result is None:
            for tf in DEPLOY_TF_ORDER:
                req = deploy.next_tier_request(tf)
                if req:
                    result = {
                        "action": "RUNTIME_SYNC_REQUEST",
                        "request": req,
                    }
                    break

        # ----------------------------------------------------
        # Policy Version Canary -> Small -> Normal / Rollback
        # ----------------------------------------------------

        if result is None:
            req = deploy.next_policy_tier_request()

            if req:
                result = {
                    "action": "POLICY_RUNTIME_SYNC_REQUEST",
                    "request": req,
                }

    if result is None and PENDING.exists() and not held:
        result = {"action": "WAIT_RUNTIME_SYNC", "request": load(PENDING, {})}

    if result is None and not enable_research:
        result = {"action": "STATUS_ONLY", "server_push_hold": held, "status": controller_status()}

    # 2) 전체 bounded ENTRY SIGNAL pool이 끝난 TF만 PROVISIONAL 후보를 만든다.
    if result is None:
        for tf in MAIN_FILL_TF_ORDER:
            if not can_accept_canary_peer(tf):
                continue
            best = strategy_research.provisional_choice(tf)
            if (
                best
                and not candidate_already_tracked(
                    tf,
                    best,
                )
            ):
                req = deploy.create_provisional_request(tf, best)
                if req:
                    result = {
                        "action": "PROVISIONAL_LOCAL_READY" if req.get("local_only") else "PROVISIONAL_CANARY_REQUEST",
                        "server_push_hold": held,
                        "request": req,
                    }
                    break

    if result is None:
        for tf in MAIN_FILL_TF_ORDER:
            if not can_accept_canary_peer(tf):
                continue
            combo = combination_research.provisional_choice(tf)
            if (
                combo
                and not candidate_already_tracked(
                    tf,
                    combo,
                )
            ):
                req = deploy.create_provisional_request(tf, combo)
                if req:
                    result = {
                        "action": "PROVISIONAL_COMBO_LOCAL_READY" if req.get("local_only") else "PROVISIONAL_COMBO_CANARY_REQUEST",
                        "server_push_hold": held,
                        "request": req,
                    }
                    break

    # EMPTY_MAIN_BOUNDED_COMBO_RECOVERY
    # MAIN-empty frames receive the next finite promotion-eligible
    # combination research opportunity before Mutation/background work.
    # Evaluation/ranking/holdout/deployment contracts remain unchanged.
    if result is None:
        for tf in empty_main_fill_tfs():
            recovery_hypotheses=combination_research.recovery_hypotheses(tf)
            if not recovery_hypotheses:
                continue

            row=combination_research.run_one(tf, recovery=True)

            _record_strategy_track(
                "EMPTY_MAIN_BOUNDED_COMBINATION",
                row if isinstance(row,dict) else {"status":"MEASURED"},
            )

            result={
                "action":"EMPTY_MAIN_BOUNDED_COMBINATION_RESEARCH",
                "server_push_hold":held,
                "timeframe":tf,
                "result":row,
            }
            break

    # 3) 초기 18개 독립 family × 5TF bounded 연구.
    if result is None:
        nxt = strategy_research.choose_next()
        if nxt:
            row = strategy_research.run_one()
            _record_strategy_track("INITIAL_18_SINGLE", row)
            result = {"action": "SINGLE_RESEARCH", "server_push_hold": held, "result": row}

    # 4) 48개 safe-grammar × 5TF bounded wave.
    if result is None:
        nxt = hypothesis_factory.choose_next()
        if nxt:
            row = hypothesis_factory.run_one()
            _record_strategy_track("SAFE_GRAMMAR_48", row)
            result = {"action": "SAFE_GRAMMAR_RESEARCH", "server_push_hold": held, "result": row}

    # 5) 단독 pool 완료 뒤 complementary bounded 조합만 연구한다.
    if result is None:
        fill_priority = (
            strategy_research.candidate_fill_priority()
        )

        combo_tf_order = (
            fill_priority
            + [
                tf
                for tf in RESEARCH_TF_ORDER
                if tf not in fill_priority
            ]
        )

        for tf in combo_tf_order:
            hypotheses = combination_research.hypotheses(tf)
            if hypotheses:
                row = combination_research.run_one(tf)
                _record_strategy_track("BOUNDED_COMBINATION", row if isinstance(row, dict) else {"status": "MEASURED"})
                result = {"action": "COMBINATION_RESEARCH", "server_push_hold": held, "timeframe": tf, "result": row}
                break

    # 6) 활성 MAIN은 동일 엔진 baseline을 정례 측정하고 challenger를 evidence dominance로만 선택한다.
    if result is None:
        for tf in DEPLOY_TF_ORDER:
            if not frame_live(tf):
                continue
            baseline = baseline_research.get(tf)
            if not baseline:
                row = baseline_research.run(tf)
                _record_strategy_track("ACTIVE_MAIN_BASELINE", row)
                result = {"action": "ACTIVE_MAIN_BASELINE_RESEARCH", "server_push_hold": held, "timeframe": tf, "result": row}
                break

    if result is None:
        for tf in DEPLOY_TF_ORDER:
            if not frame_live(tf):
                continue
            challenger = challenger_research.choice(tf)
            if not challenger:
                continue
            dfr = (deploy.state().get("frames") or {}).get(tf) or {}
            same = str(dfr.get("strategy") or "").upper() == str(challenger.get("strategy") or "").upper()
            active_tier = str(dfr.get("tier") or "").upper() in {"LOCAL_PROVISIONAL_READY", "PENDING_CANARY_SYNC", "CANARY", "SMALL", "NORMAL"}
            if same and active_tier:
                continue
            req = deploy.create_provisional_request(tf, challenger)
            if req:
                _record_strategy_track("ACTIVE_MAIN_CHALLENGER", {"status": "CANDIDATE", "strategy": challenger.get("strategy"), "timeframe": tf})
                result = {
                    "action": "ACTIVE_MAIN_CHALLENGER_LOCAL_READY" if req.get("local_only") else "ACTIVE_MAIN_CHALLENGER_CANARY_REQUEST",
                    "server_push_hold": held,
                    "request": req,
                }
                break

    # 7) ENTRY_RISK/EXIT/REENTRY/SYMBOL/PORTFOLIO/REGIME/EXECUTION/DIRECTION 정례 연구.
    if result is None:
        task = task_queue.select_next(exclude={"STRATEGY"})
        if task:
            row = policy_research.run_task(task)
            result = {
                "action": "LIFECYCLE_POLICY_RESEARCH",
                "server_push_hold": held,
                "track": task.get("track"),
                "task": task,
                "result": row,
            }

    # 8) 새 입력이 없으면 기존 descriptive evidence cursor만 갱신하고 중복 연구는 하지 않는다.
    if result is None:
        ev = evidence_research.run_next()
        result = {
            "action": "NO_NEW_BOUNDED_INPUT",
            "server_push_hold": held,
            "evidence": ev,
            "tracks": list(governance.EXPECTED_TRACKS),
        }

    if isinstance(result, dict):
        result.setdefault(
            "research_architecture_policy_id",
            architecture.get("policy_id"),
        )

        result.setdefault(
            "research_scheduler_status",
            scheduler_contract.get("status"),
        )

    governance.assert_kernel_unchanged(kernel_before)
    return result


# ============================================================
# 자체검증 / Health
# ============================================================


def selftest() -> dict:
    files = strategy_research.family_files()
    bad = []
    for path in files:
        try:
            meta = strategy_research.load_meta(path)
            if not meta.get("name"):
                bad.append(str(path))
        except Exception:
            bad.append(str(path))
    cv = governance.validate_constitution()
    recipes = hypothesis_factory.recipes()
    queue = task_queue.status()
    return {
        "ok": not bad and cv.get("ok") and len(files) == 18 and len(recipes) == 48 and set(queue.get("tracks") or {}) == set(governance.EXPECTED_TRACKS),
        "family_count": len(files),
        "safe_grammar_count": len(recipes),
        "bad_families": bad,
        "constitution": cv,
        "track_count": len(queue.get("tracks") or {}),
        "trial73": "COMPLETED_DO_NOT_RERUN",
        "legacy_trial_counters": {"search": 73, "parameter": 73},
        "controller_status": controller_status(),
    }


def health() -> dict:
    return {
        "ok": bool(governance.validate_constitution().get("ok")),
        "root": str(ROOT),
        "config_exists": (ROOT / "strategy_config.json").exists(),
        "runtime_db_exists": (ROOT / "data" / "server_runtime" / "history.db").exists(),
        "pending_runtime_sync": PENDING.exists(),
        "server_push_hold": PUSH_HOLD.exists(),
        "status": controller_status(),
    }


# ============================================================
# 메인
# ============================================================


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["once", "status", "selftest", "health", "managed-dry-run", "loop"], default="once")
    parser.add_argument("--enable-research", action="store_true")
    parser.add_argument("--skip-server-health", action="store_true")
    parser.add_argument("--check-inputs", action="store_true")
    parser.add_argument("--export-inputs", type=Path)
    parser.add_argument("--sleep-sec", type=int, default=60)
    parser.add_argument("--max-cycles", type=int, default=0)
    args = parser.parse_args()

    if args.mode == "status":
        print(json.dumps(controller_status(), ensure_ascii=False, indent=2, default=str))
        return
    if args.mode == "selftest":
        print(json.dumps(selftest(), ensure_ascii=False, indent=2, default=str))
        return
    if args.mode == "health":
        print(json.dumps(health(), ensure_ascii=False, indent=2, default=str))
        return
    if args.mode == "managed-dry-run":
        if args.export_inputs is not None:
            try:
                with lock():
                    result = managed_dry_run(include_inputs=True, export_inputs=args.export_inputs)
            except RuntimeError as exc:
                if str(exc) == "RESEARCH_ALREADY_RUNNING":
                    print(json.dumps({"action": "SKIP", "reason": str(exc), "export_created": False}))
                    raise SystemExit(3)
                raise
        else:
            result = managed_dry_run(include_inputs=args.check_inputs)
        checked = args.check_inputs or args.export_inputs is not None
        output = {key: result[key] for key in ("ok", "input_inventory", "mutated", "orders_touched")} if checked else result
        if "export" in result:
            output["export"] = result["export"]
        print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
        if checked and not result["ok"]:
            raise SystemExit(2)
        return

    def one():
        try:
            with lock():
                # MANAGED_RESEARCH_CLI_WIRE_V1
                #
                # Roadmap Manager acquires a scoped SERVER_PUSH_HOLD
                # before a bounded research action. In that exact state,
                # route research into the unified Stage10 lifecycle
                # manager. HOLD-off execution keeps the legacy run_once
                # runtime/release path unchanged.
                if args.enable_research and PUSH_HOLD.exists():
                    return managed_research_once(True)

                return run_once(
                    enable_research=args.enable_research
                )
        except RuntimeError as exc:
            if str(exc) == "RESEARCH_ALREADY_RUNNING":
                return {"action": "SKIP", "reason": "RESEARCH_ALREADY_RUNNING"}
            raise

    if args.mode == "loop":
        cycles = 0
        while True:
            print(json.dumps(one(), ensure_ascii=False, default=str), flush=True)
            cycles += 1
            if args.max_cycles > 0 and cycles >= args.max_cycles:
                break
            time.sleep(max(1, args.sleep_sec))
        return

    print(json.dumps(one(), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
