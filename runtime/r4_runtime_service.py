#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 파일명: r4_runtime_service.py
"""기존 Gate/Risk/Order를 exact Strategy Instance R4 Runtime에 연결하는 독점 실행 서비스."""
from __future__ import annotations

# ================= 임포트 =================
import hashlib
import json
import logging
import os
import time
from contextlib import closing
from pathlib import Path
from decimal import Decimal, ROUND_DOWN, ROUND_UP

import candidate as candidate_contract
import capital_allocator
import gate
import late_entry
from r4_funding_feed import reconcile_funding_income
from r4_performance_tiering import tier_priority_rank, transition_tier_config_row
import runner as frozen_kernel
import strategy_runtime
from multi_strategy_runtime import MultiStrategyRuntime
from r4_exchange_adapter import BinanceR4Exchange
from r4_user_stream import R4UserStream
import signal

# ================= 설정 =================
BASE = Path(os.getenv("BOT_BASE_DIR", "/home/ec2-user/bot"))
STRATEGY_CONFIG_FILE = Path(os.getenv("STRATEGY_CONFIG_FILE", str(BASE / "strategy_config.json")))
LOG_FILE = BASE / "logs" / "runner.log"
R4_LIVE_TIERS = {"CANARY", "SMALL", "NORMAL"}

R4_REENTRY_TF_SECONDS = {
    "30M": 1800,
    "1H": 3600,
    "6H": 21600,
    "12H": 43200,
    "1D": 86400,
    "1W": 604800,
}

ADAPTIVE_CAPITAL_BOOST_POLICY = (
    "SUB_100M_AGGRESSIVE_GROWTH_ADAPTIVE_CAPITAL_BOOST_V1"
)
ADAPTIVE_CAPITAL_BOOST_HARD_MAX = {
    "CANARY": 1.50,
    "SMALL": 2.00,
    "NORMAL": 2.50,
}


def _r4_instance_entry_capital_weight(instance, tier=None):
    """Return the effective capital weight for NEW entries only.

    Legacy instances without adaptive metadata keep the historical base Tier
    weight. Adaptive instances must carry internally consistent bounded metadata;
    malformed metadata fails closed by returning zero.
    """
    instance = instance if isinstance(instance, dict) else {}
    tier = str(tier or instance.get("tier") or "").upper()
    base = float(strategy_runtime.r4_tier_weight(tier) or 0.0)
    if base <= 0.0:
        return 0.0, {"source": "INVALID_TIER", "base_tier_weight": base}

    policy = str(instance.get("adaptive_capital_boost_policy") or "")
    if not policy:
        return base, {
            "source": "LEGACY_BASE_TIER_WEIGHT",
            "base_tier_weight": base,
            "adaptive": False,
        }
    if policy != ADAPTIVE_CAPITAL_BOOST_POLICY:
        return 0.0, {
            "source": "ADAPTIVE_POLICY_UNKNOWN_FAIL_CLOSED",
            "base_tier_weight": base,
            "adaptive": True,
        }

    try:
        configured_base = float(instance.get("capital_base_weight"))
        boost_multiplier = float(instance.get("adaptive_capital_boost_multiplier"))
        capital_weight = float(instance.get("capital_weight"))
    except Exception:
        return 0.0, {
            "source": "ADAPTIVE_METADATA_INVALID_FAIL_CLOSED",
            "base_tier_weight": base,
            "adaptive": True,
        }

    hard_max = float(ADAPTIVE_CAPITAL_BOOST_HARD_MAX.get(tier) or 0.0)
    tol = max(1e-12, abs(base) * 1e-9)
    if (
        hard_max <= 0.0
        or abs(configured_base - base) > tol
        or boost_multiplier < 1.0
        or boost_multiplier > hard_max + 1e-12
        or capital_weight <= 0.0
        or capital_weight > (base * hard_max) + tol
        or capital_weight > (base * boost_multiplier) + tol
    ):
        return 0.0, {
            "source": "ADAPTIVE_METADATA_BOUNDS_FAIL_CLOSED",
            "base_tier_weight": base,
            "adaptive": True,
            "configured_base_weight": configured_base,
            "boost_multiplier": boost_multiplier,
            "capital_weight": capital_weight,
            "hard_max_multiplier": hard_max,
        }

    return capital_weight, {
        "source": "STRATEGY_INSTANCE_ADAPTIVE_CAPITAL_WEIGHT",
        "base_tier_weight": base,
        "adaptive": True,
        "boost_multiplier": boost_multiplier,
        "hard_max_multiplier": hard_max,
    }

# ================= 로그 =================
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
if not logging.getLogger().handlers:
    logging.basicConfig(
        filename=str(LOG_FILE),
        level=logging.INFO,
        encoding="utf-8",
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
logger = logging.getLogger("r4_runtime_service")
_log_dedupe = {}


def _log(level, key, message, sec=60):
    """반복 이벤트/실패 로그를 bounded dedupe한다."""
    now = time.monotonic()
    token = str(key)
    last = _log_dedupe.get(token)
    if last is not None and now - last < float(sec):
        return
    _log_dedupe[token] = now
    if len(_log_dedupe) > 2048:
        cutoff = now - 600.0
        for old_key in [k for k, stamp in _log_dedupe.items() if stamp < cutoff]:
            _log_dedupe.pop(old_key, None)
    getattr(logger, level)(message)

# ================= 유틸 =================
def _config():
    try:
        return json.loads((BASE / "config.json").read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _symbols(cfg):
    rows = cfg.get("SYMBOLS") or {}
    if isinstance(rows, dict):
        return [str(k).upper() for k, v in rows.items() if v]
    return [str(x).upper() for x in rows]


def _stable_hash(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _r4_reentry_earliest_ts(exit_ts, timeframe, cooldown_bars):
    exit_ts=int(exit_ts or 0)
    timeframe=str(timeframe or "").upper()
    cooldown_bars=int(cooldown_bars)
    seconds=int(R4_REENTRY_TF_SECONDS.get(timeframe) or 0)
    if exit_ts<=0 or seconds<=0 or cooldown_bars not in {0,1,2}:
        raise RuntimeError("R4_REENTRY_COOLDOWN_CONTRACT_INVALID")
    current_bar_start=(exit_ts//seconds)*seconds
    return int(current_bar_start + seconds*(1+cooldown_bars))


def _atomic_strategy_config(obj):
    """서버 Tier single-writer가 strategy_config.json을 원자 갱신한다."""
    tmp = STRATEGY_CONFIG_FILE.with_name(
        STRATEGY_CONFIG_FILE.name + "." + str(os.getpid()) + ".tmp"
    )
    tmp.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, STRATEGY_CONFIG_FILE)
    strategy_runtime.reload()

# ================= R4 서비스 =================
class R4RuntimeService:
    def __init__(self):
        if os.getenv("R4_RUNTIME_MODE", "LOCAL_SHADOW").upper() != "EXCLUSIVE_LIVE":
            raise RuntimeError("SERVICE_REQUIRES_EXCLUSIVE_LIVE")
        if os.getenv("R4_LIVE_CUTOVER_VERIFIED") != "1":
            raise RuntimeError("R4_LIVE_CUTOVER_NOT_VERIFIED")
        if os.getenv("R4_LEGACY_WRITER_STOPPED") != "1":
            raise RuntimeError("R4_LEGACY_WRITER_NOT_STOPPED")

        runtime_validation = strategy_runtime.validate()
        if not runtime_validation.get("ok"):
            raise RuntimeError("STRATEGY_RUNTIME_INVALID:" + ",".join(runtime_validation.get("errors") or []))

        self.exchange = BinanceR4Exchange(frozen_kernel.get_order())
        self.runtime = MultiStrategyRuntime(exchange=self.exchange)
        self.user_stream = R4UserStream(self.runtime, self.exchange)
        self.cfg = _config()
        self._register_config_instances()

    def close(self):
        # R4_GRACEFUL_SYSTEMD_SHUTDOWN_V1
        try:
            self.user_stream.close()
        finally:
            self.runtime.close()

    # ================= Runtime 등록 =================
    def _instance_trailing_version(self, row):
        explicit = str(row.get("trailing_policy_version") or "").strip()
        if explicit:
            return explicit
        snapshot = row.get("policy_snapshot") or {}
        versions = snapshot.get("policy_versions") if isinstance(snapshot, dict) else {}
        versions = versions if isinstance(versions, dict) else {}
        value = str(versions.get("EXIT") or "").strip()
        if not value:
            raise RuntimeError("R4_INSTANCE_TRAILING_POLICY_VERSION_MISSING")
        return value

    def _register_config_instances(self):
        """서버 config의 승인된 exact Strategy Instance를 ledger canonical ID로 등록한다."""
        rows = strategy_runtime.r4_strategy_instances()
        for instance_id, row in rows.items():
            if row.get("approved_for_server_release") is not True:
                continue
            tier = str(row.get("tier") or "").upper()
            if tier not in R4_LIVE_TIERS:
                raise RuntimeError("R4_INSTANCE_TIER_INVALID:" + instance_id)
            registered = self.runtime.ledger.register_instance(
                strategy_id=str(row.get("strategy_id") or ""),
                timeframe=str(row.get("timeframe") or ""),
                exact_version=str(row.get("exact_strategy_version") or ""),
                policy_bundle=str(row.get("strategy_policy_bundle_version") or ""),
                tier=tier,
                trailing_policy_version=self._instance_trailing_version(row),
                exact_instance_id=instance_id,
            )
            if registered != instance_id:
                raise RuntimeError("R4_INSTANCE_LEDGER_ID_MISMATCH:" + instance_id)

            # Tier transition은 DB가 먼저 commit되고 config가 뒤따르므로 crash 복구 시 ledger가 권위다.
            ledger_row = self.runtime.ledger.instance_state(instance_id)
            ledger_tier = str(ledger_row.get("tier") or "").upper()
            if ledger_tier != tier:
                self._persist_tier_state(
                    instance_id,
                    ledger_tier,
                    "RECOVER_LEDGER_TIER_AFTER_CONFIG_WRITE_GAP",
                    entry_blocked=bool(row.get("entry_blocked", False)),
                    rollback_requested=bool(row.get("rollback_requested_to_local", False)),
                )

            self._ingest_config_evidence(instance_id, row)

    def _ingest_config_evidence(self, instance_id, row):
        """명시적으로 전달된 exact-instance evidence만 원장에 기록한다. 추정/백필은 금지한다."""
        evidence = row.get("scale_evidence") or {}
        if not isinstance(evidence, dict):
            return
        exact_version = str(row.get("exact_strategy_version") or "")
        for raw_name, raw_value in evidence.items():
            name = str(raw_name or "").upper().strip()
            if not name:
                continue
            # R4_SERVER_OWNED_SCALE_EVIDENCE_V1
            # Live-runtime / ledger authority; config evidence cannot supersede these.
            if name in {
                "EXACT_STRATEGY_INSTANCE_LIFECYCLE",
                "ACTUAL_SMALL_LIVE_LIFECYCLE",
                "AVAILABLE_MARGIN_PASS",
                "RELATIVE_TIER_COMPETITION_PASS",
                "STRATEGY_LEG_LEDGER_RECONCILE_PASS",
                "NO_CRITICAL_HARD_FAIL",
            }:
                continue
            if isinstance(raw_value, bool):
                passed = raw_value
                detail = {"source": "STRATEGY_CONFIG_EXPLICIT_EVIDENCE"}
                evidence_key = ""
            elif isinstance(raw_value, dict):
                if "passed" not in raw_value:
                    continue
                if raw_value.get("exact_strategy_version") not in (None, "", exact_version):
                    raise RuntimeError("R4_SCALE_EVIDENCE_VERSION_MISMATCH:" + instance_id + ":" + name)
                passed = raw_value.get("passed") is True
                detail = {
                    k: v
                    for k, v in raw_value.items()
                    if k not in {"passed", "evidence_key"}
                }
                evidence_key = str(raw_value.get("evidence_key") or "")
            else:
                continue
            if not evidence_key:
                evidence_key = "CFG_" + _stable_hash(
                    {
                        "instance_id": instance_id,
                        "exact_version": exact_version,
                        "tier": str(row.get("tier") or "").upper(),
                        "name": name,
                        "passed": passed,
                        "detail": detail,
                    }
                )[:32]
            self.runtime.record_scale_evidence(
                instance_id,
                name,
                passed,
                detail,
                evidence_key,
            )

    # ================= Tier Controller =================
    def _persist_tier_state(
        self,
        instance_id,
        tier,
        reason,
        entry_blocked=False,
        rollback_requested=False,
    ):
        cfg = json.loads(STRATEGY_CONFIG_FILE.read_text(encoding="utf-8-sig"))
        runtime = cfg.setdefault("r4_runtime", {})
        rows = runtime.setdefault("strategy_instances", {})
        row = rows.get(instance_id)
        if not isinstance(row, dict):
            raise RuntimeError("R4_CONFIG_INSTANCE_MISSING:" + instance_id)
        previous_tier = str(row.get("tier") or "").upper()
        previous_base = float(strategy_runtime.r4_tier_weight(previous_tier) or 0.0)
        previous_weight = 0.0
        try:
            previous_weight = float(row.get("capital_weight") or 0.0)
        except Exception:
            previous_weight = 0.0

        row["tier"] = str(tier).upper()
        target_base = float(strategy_runtime.r4_tier_weight(tier) or 0.0)
        if str(row.get("adaptive_capital_boost_policy") or "") == ADAPTIVE_CAPITAL_BOOST_POLICY:
            # Preserve the already-validated effective scale ratio across a tier
            # transition; never auto-increase it here. Local portfolio governance
            # may later issue a validated higher weight.
            effective_ratio = (
                previous_weight / previous_base
                if previous_base > 0.0 and previous_weight > 0.0
                else 0.0
            )
            hard_max = float(ADAPTIVE_CAPITAL_BOOST_HARD_MAX.get(str(tier).upper()) or 0.0)
            if effective_ratio <= 0.0 or hard_max <= 0.0:
                row["capital_weight"] = 0.0
                row["entry_blocked"] = True
                row["tier_reason"] = "ADAPTIVE_CAPITAL_METADATA_FAIL_CLOSED"
            else:
                row["capital_base_weight"] = target_base
                row["capital_weight"] = target_base * min(effective_ratio, hard_max)
        else:
            row["capital_weight"] = target_base
        row["tier_updated_at"] = int(time.time())
        row["tier_reason"] = str(reason)
        row["entry_blocked"] = bool(entry_blocked)
        row["rollback_requested_to_local"] = bool(rollback_requested)
        if rollback_requested:
            row["rollback_target"] = "PREVIOUS_VALIDATED_VERSION"
        _atomic_strategy_config(cfg)

    def _apply_tier_decision(self, instance_id, decision):
        action = str(decision.get("action") or "HOLD").upper()
        current = str(decision.get("current_tier") or "").upper()
        target = str(decision.get("target_tier") or "").upper()
        reason = str(decision.get("reason") or action)

        if action == "ROLLBACK_SCALE_DOWN":
            self._persist_tier_state(
                instance_id,
                current,
                reason,
                entry_blocked=True,
                rollback_requested=bool(decision.get("rollback_requested_to_local", False)),
            )
            return {
                "changed": False,
                "action": "HOLD",
                "reason": "SAFETY_LOCK_WITHOUT_TIER_JUMP",
            }

        if action in {"PROMOTE", "DEMOTE", "ROLLBACK_SCALE_DOWN"}:
            evidence_key = "AUTO_TIER_" + _stable_hash(
                {
                    "instance_id": instance_id,
                    "current": current,
                    "target": target,
                    "reason": reason,
                    "failed": decision.get("failed") or [],
                }
            )[:32]
            result = self.runtime.apply_tier(instance_id, target, evidence_key)
            self._persist_tier_state(
                instance_id,
                target,
                reason,
                entry_blocked=bool(decision.get("entry_blocked", False)),
                rollback_requested=bool(decision.get("rollback_requested_to_local", False)),
            )
            _log(
                "info",
                "TIER:" + instance_id + ":" + target + ":" + evidence_key,
                f"[주문][R4][TIER] instance={instance_id} action={action} {current}->{target} reason={reason}",
                sec=300,
            )
            return result

        if action == "HOLD" and decision.get("entry_blocked") is True:
            row = strategy_runtime.r4_strategy_instance(instance_id)
            if row and row.get("entry_blocked") is not True:
                self._persist_tier_state(
                    instance_id,
                    current,
                    reason,
                    entry_blocked=True,
                    rollback_requested=bool(decision.get("rollback_requested_to_local", False)),
                )
        return {"changed": False, "action": action, "reason": reason}

    # R4_SERVER_LIVE_SCALE_EVIDENCE_V2
    def _record_server_owned_boolean_evidence(
        self,
        instance_id,
        evidence_name,
        passed,
        detail=None,
    ):
        evidence_name = str(
            evidence_name or ""
        ).upper().strip()

        if not evidence_name:
            raise ValueError(
                "SERVER_SCALE_EVIDENCE_NAME_REQUIRED"
            )

        if (
            evidence_name == "NO_CRITICAL_HARD_FAIL"
            and not bool(passed)
        ):
            failed = getattr(
                self,
                "_critical_fail_instances_this_cycle",
                None,
            )

            if not isinstance(failed, set):
                failed = set()
                self._critical_fail_instances_this_cycle = failed

            failed.add(str(instance_id))

        # Authoritative current instance/tier is read directly
        # from the transactional R4 ledger.
        with closing(self.runtime.ledger.connect()) as con:
            instance_row = con.execute(
                """
                SELECT tier
                FROM r4_strategy_instances
                WHERE instance_id=?
                """,
                (str(instance_id),),
            ).fetchone()

        if instance_row is None:
            raise RuntimeError(
                "SERVER_SCALE_EVIDENCE_INSTANCE_MISSING"
            )

        try:
            tier = str(
                instance_row["tier"] or ""
            ).upper()
        except Exception:
            tier = str(
                instance_row[0] or ""
            ).upper()

        if tier not in R4_LIVE_TIERS:
            raise RuntimeError(
                "SERVER_SCALE_EVIDENCE_TIER_INVALID"
            )

        payload = dict(detail or {})

        latest = self.runtime.ledger.latest_tier_evidence(
            instance_id,
            tier,
        ).get(evidence_name)

        if isinstance(latest, dict):

            previous_detail = (
                latest.get("detail")
                if isinstance(
                    latest.get("detail"),
                    dict,
                )
                else {}
            )

            if (
                bool(latest.get("passed"))
                    == bool(passed)
                and str(
                    previous_detail.get("source")
                    or ""
                )
                == str(
                    payload.get("source")
                    or ""
                )
            ):
                return latest

        payload["observed_ts"] = int(
            time.time()
        )

        return self.runtime.record_scale_evidence(
            instance_id,
            evidence_name,
            bool(passed),
            payload,
            evidence_key=(
                "R4SERVER:"
                + str(instance_id)
                + ":"
                + tier
                + ":"
                + evidence_name
                + ":"
                + str(int(bool(passed)))
                + ":"
                + str(time.time_ns())
            ),
        )

    def _refresh_available_margin_evidence(
        self,
        active_rows,
    ):
        try:
            available = float(
                self.exchange.available_margin_usdt()
            )

            passed = available > 0.0

            detail = {
                "source":
                    "LIVE_EXCHANGE_ACCOUNT_RESOURCE_EVIDENCE",
                "available_margin_usdt":
                    available,
            }

        except Exception as exc:
            passed = False

            detail = {
                "source":
                    "LIVE_EXCHANGE_ACCOUNT_RESOURCE_EVIDENCE",
                "error":
                    type(exc).__name__,
            }

        for instance_id, row in active_rows.items():

            if row.get(
                "approved_for_server_release"
            ) is not True:
                continue

            tier = str(
                row.get("tier") or ""
            ).upper()

            if tier not in R4_LIVE_TIERS:
                continue

            self._record_server_owned_boolean_evidence(
                instance_id,
                "AVAILABLE_MARGIN_PASS",
                passed,
                detail,
            )

        return passed

    def _record_successful_cycle_no_critical(
        self,
        active_rows,
    ):
        if not self.runtime.entry_admission_enabled:
            return False

        failed = getattr(
            self,
            "_critical_fail_instances_this_cycle",
            set(),
        )

        if not isinstance(failed, set):
            failed = set()

        for instance_id, row in active_rows.items():

            if row.get(
                "approved_for_server_release"
            ) is not True:
                continue

            tier = str(
                row.get("tier") or ""
            ).upper()

            if tier not in R4_LIVE_TIERS:
                continue

            if str(instance_id) in failed:
                continue

            self._record_server_owned_boolean_evidence(
                instance_id,
                "NO_CRITICAL_HARD_FAIL",
                True,
                {
                    "source":
                        "R4_FULL_CYCLE_SAFETY_PASS",
                },
            )

        return True


    def _recover_transient_leg_management_safety_latch(self, instance_id, row, controller):
        # 파일명: r4_runtime_service.py / 새 전체 대조로 해결이 증명된 자동 안전 차단만 복구한다.
        if not getattr(self,"_reconcile_cycle_pass",False) or not self.runtime.entry_admission_enabled:
            return False
        if str(instance_id) in getattr(self,"_critical_fail_instances_this_cycle",set()):
            return False
        if not isinstance(row, dict):
            return False

        if row.get("entry_blocked") is not True:
            return False

        if row.get("rollback_requested_to_local") is not True:
            return False

        if str(row.get("tier_reason") or "") != "SAFETY_EVIDENCE_FAIL":
            return False

        rollback_target = str(row.get("rollback_target") or "")
        if rollback_target not in ("", "PREVIOUS_VALIDATED_VERSION"):
            return False

        tier = str(row.get("tier") or "").upper()
        if tier not in R4_LIVE_TIERS:
            return False

        exact_version = str(row.get("exact_strategy_version") or "")
        if not exact_version:
            return False

        block_ts = int(row.get("tier_updated_at") or 0)
        if block_ts <= 0:
            return False

        ledger = self.runtime.ledger
        con = ledger.connect()

        try:
            failed_rows = con.execute(
                "SELECT evidence_name,detail_json,ts FROM r4_tier_evidence "
                "WHERE instance_id=? AND exact_version=? AND tier=? AND passed=0 "
                "AND ts>=? AND ts<=? ORDER BY ts DESC,id DESC",
                (
                    str(instance_id),
                    exact_version,
                    tier,
                    max(0, block_ts - 180),
                    block_ts + 10,
                ),
            ).fetchall()
        finally:
            con.close()

        if not failed_rows:
            return False

        failed_names = {str(x["evidence_name"] or "").upper() for x in failed_rows}
        leg_management=failed_names=={"NO_CRITICAL_HARD_FAIL"}
        basket_reconcile=failed_names=={"NO_CRITICAL_HARD_FAIL","STRATEGY_LEG_LEDGER_RECONCILE_PASS"}
        if not leg_management and not basket_reconcile:
            return False
        recovery_source="TRANSIENT_LEG_MANAGEMENT_RUNTIMEERROR" if leg_management else "VERIFIED_BASKET_RECONCILE_RECOVERY"
        for failed_row in failed_rows:
            try:
                detail=json.loads(failed_row["detail_json"] or "{}")
            except (TypeError,ValueError):
                return False
            source=str(detail.get("source") or "").upper()
            if leg_management:
                if source!="LEG_MANAGEMENT" or "RUNTIMEERROR" not in str(detail.get("error") or "").upper():
                    return False
            elif str(failed_row["evidence_name"]).upper()=="NO_CRITICAL_HARD_FAIL":
                if source!="BASKET_RECONCILE_MISMATCH":
                    return False
            elif source!="BASKET_RECONCILE":
                return False

        evidence = ledger.latest_tier_evidence(instance_id, tier) or {}

        hard = evidence.get("NO_CRITICAL_HARD_FAIL")
        if not isinstance(hard, dict):
            return False

        if int(hard.get("passed") or 0) != 1:
            return False

        if int(hard.get("ts") or 0) <= block_ts:
            return False

        reconcile = evidence.get("STRATEGY_LEG_LEDGER_RECONCILE_PASS")
        if not isinstance(reconcile, dict):
            return False

        if int(reconcile.get("passed") or 0) != 1 or int(reconcile.get("ts") or 0)<=block_ts:
            return False

        decision = self.runtime.evaluate_tier(instance_id, controller)
        if not isinstance(decision, dict):
            return False

        if decision.get("entry_blocked") is True:
            return False

        if str(decision.get("reason") or "") == "SAFETY_EVIDENCE_FAIL":
            return False

        con = ledger.connect()

        try:
            active = con.execute(
                "SELECT symbol,position_side,COALESCE(SUM(owned_qty_units),0) AS owned "
                "FROM r4_active_legs WHERE instance_id=? AND status='ACTIVE' "
                "GROUP BY symbol,position_side",
                (str(instance_id),),
            ).fetchall()

            for basket in active:
                symbol = str(basket["symbol"] or "").upper()
                side = str(basket["position_side"] or "").upper()
                owned = int(basket["owned"] or 0)

                snap = con.execute(
                    "SELECT ledger_qty_units,exchange_qty_units,mismatch,updated_ts "
                    "FROM r4_basket_snapshots WHERE symbol=? AND position_side=?",
                    (symbol, side),
                ).fetchone()

                if snap is None:
                    return False

                ledger_qty = int(snap["ledger_qty_units"] or 0)
                exchange_qty = int(snap["exchange_qty_units"] or 0)
                mismatch = int(snap["mismatch"] or 0)

                # 여러 전략이 같은 바스켓을 보유하면 전체 원장 수량과 비교한다.
                total_owned=int(con.execute(
                    "SELECT COALESCE(SUM(owned_qty_units),0) FROM r4_active_legs WHERE symbol=? AND position_side=? AND owned_qty_units>0",
                    (symbol,side),
                ).fetchone()[0] or 0)
                if (mismatch!=0 or total_owned<owned or ledger_qty!=total_owned or exchange_qty!=total_owned
                    or int(snap["updated_ts"] or 0)<int(getattr(self,"_reconcile_cycle_started_at",0))):
                    return False
        finally:
            con.close()

        cfg = json.loads(STRATEGY_CONFIG_FILE.read_text(encoding="utf-8-sig"))
        rows = cfg.setdefault("r4_runtime", {}).setdefault("strategy_instances", {})
        fresh = rows.get(instance_id)

        if not isinstance(fresh, dict):
            return False

        if str(fresh.get("exact_strategy_version") or "") != exact_version:
            return False

        if str(fresh.get("tier") or "").upper() != tier:
            return False

        if fresh.get("entry_blocked") is not True:
            return False

        if fresh.get("rollback_requested_to_local") is not True:
            return False

        if str(fresh.get("tier_reason") or "") != "SAFETY_EVIDENCE_FAIL":
            return False

        if int(fresh.get("tier_updated_at") or 0)!=block_ts:
            return False
        current_target = str(fresh.get("rollback_target") or "")
        if current_target not in ("", "PREVIOUS_VALIDATED_VERSION"):
            return False

        fresh["entry_blocked"] = False
        fresh["rollback_requested_to_local"] = False
        fresh.pop("rollback_target", None)
        fresh["tier_reason"] = "SAFETY_EVIDENCE_RECOVERED_"+recovery_source
        fresh["tier_updated_at"] = int(time.time())

        _atomic_strategy_config(cfg)

        _log(
            "info",
            "STALE_SAFETY_RECOVER:" + str(instance_id),
            "[주문][R4][SAFETY_RECOVER] instance="
            + str(instance_id)
            + " tier="
            + tier
            + " source="+recovery_source,
            sec=60,
        )

        return True

    def _persist_performance_tier_state(self, instance_id, tier, reason):
        cfg=json.loads(STRATEGY_CONFIG_FILE.read_text(encoding="utf-8-sig"))
        runtime=cfg.get("r4_runtime") or {}
        rows=runtime.get("strategy_instances") or {}
        row=rows.get(instance_id)
        if not isinstance(row,dict):
            raise RuntimeError("R4_CONFIG_INSTANCE_MISSING:"+instance_id)
        controller=runtime.get("tier_controller") or {}
        weights=controller.get("tier_weights") if isinstance(controller,dict) else {}
        weights=dict(weights) if isinstance(weights,dict) else {}
        for item in ("CANARY","SMALL","NORMAL"):
            weights.setdefault(item,strategy_runtime.r4_tier_weight(item))
        try:
            updated=transition_tier_config_row(
                row,tier,weights,ADAPTIVE_CAPITAL_BOOST_POLICY,
                ADAPTIVE_CAPITAL_BOOST_HARD_MAX,
            )
        except ValueError as exc:
            raise RuntimeError("PERFORMANCE_TIER_WEIGHT_OR_STEP_INVALID") from exc
        row.update(updated)
        row["tier_updated_at"]=int(time.time())
        row["tier_reason"]=str(reason)
        _atomic_strategy_config(cfg)

    def _apply_performance_tier_decision(self, instance_id, decision):
        action=str(decision.get("action") or "HOLD").upper()
        current=str(decision.get("current_tier") or "").upper()
        target=str(decision.get("target_tier") or "").upper()
        if action not in {"PROMOTE","DEMOTE"}:
            return {"changed":False,"action":action,"reason":str(decision.get("reason") or action)}
        if target not in {
            "CANARY":{"SMALL"},
            "SMALL":{"CANARY","NORMAL"},
            "NORMAL":{"SMALL"},
        }.get(current,set()):
            raise RuntimeError("INVALID_PERFORMANCE_TIER_STEP:"+current+"->"+target)
        evidence_key=str(decision.get("evidence_key") or "")
        if not evidence_key:
            raise RuntimeError("PERFORMANCE_EVIDENCE_KEY_REQUIRED")
        result=self.runtime.apply_tier(instance_id,target,evidence_key)
        if not result.get("changed"):
            return result
        try:
            self._persist_performance_tier_state(
                instance_id,target,str(decision.get("reason") or action),
            )
        except Exception as exc:
            rollback_key="PERF_TIER_CONFIG_RESTORE_"+_stable_hash(
                {"instance_id":instance_id,"current":current,"target":target,"evidence_key":evidence_key}
            )[:32]
            try:
                self.runtime.apply_tier(instance_id,current,rollback_key)
            except Exception as rollback_exc:
                raise RuntimeError("PERFORMANCE_TIER_CONFIG_WRITE_AND_LEDGER_ROLLBACK_FAILED") from rollback_exc
            raise RuntimeError("PERFORMANCE_TIER_CONFIG_WRITE_FAILED_LEDGER_RESTORED") from exc
        _log(
            "info","PERFORMANCE_TIER:"+instance_id+":"+target+":"+evidence_key,
            f"[주문][R4][PERFORMANCE_TIER] instance={instance_id} {current}->{target} reason={decision.get('reason')}",
            sec=300,
        )
        return result

    def _sync_performance_funding(self, active_rows, funding_config):
        if not isinstance(funding_config,dict) or funding_config.get("enabled") is not True:
            return {"complete":False,"reason":"FUNDING_COLLECTION_DISABLED"}
        target_ids=set()
        start_ts=None
        end_ts=None
        for instance_id,row in active_rows.items():
            if row.get("approved_for_server_release") is not True:
                continue
            if str(row.get("tier") or "").upper() not in R4_LIVE_TIERS:
                continue
            cohort=self.runtime.ledger.performance_tier_cohort(instance_id)
            for item in cohort["legs"]:
                if item.get("funding_coverage") and item["funding_coverage"].get("complete"):
                    continue
                leg=item["leg"]
                exit_ts=max(
                    (int(evidence.get("exchange_ts") or 0) for evidence in item["evidence"] if evidence.get("kind")=="EXIT_FILL"),
                    default=0,
                )
                leg_start=int(leg.get("created_ts") or 0)
                if leg_start<=0 or exit_ts<=0:
                    return {"complete":False,"reason":"FUNDING_LEG_BOUNDARIES_UNVERIFIED","held_instances":[instance_id]}
                target_ids.add(instance_id)
                start_ts=leg_start if start_ts is None else min(start_ts,leg_start)
                end_ts=exit_ts if end_ts is None else max(end_ts,exit_ts)
        if not target_ids:
            return {"complete":True,"needed":False}
        try:
            interval=int(funding_config.get("poll_interval_seconds",300))
        except (TypeError,ValueError) as exc:
            raise RuntimeError("INVALID_FUNDING_POLL_INTERVAL") from exc
        if interval<=0:
            raise RuntimeError("INVALID_FUNDING_POLL_INTERVAL")
        now=time.monotonic()
        if now-float(getattr(self,"_last_performance_funding_sync",0.0))<interval:
            return {"complete":False,"reason":"FUNDING_SYNC_INTERVAL_NOT_ELAPSED","held_instances":sorted(target_ids)}
        self._last_performance_funding_sync=now
        try:
            result=reconcile_funding_income(
                self.exchange,self.runtime.ledger,target_ids,
                int(start_ts)*1000,(int(end_ts)+1)*1000-1,
            )
        except Exception as exc:
            _log(
                "error","R4_PERFORMANCE_FUNDING_SYNC_FAIL:"+type(exc).__name__,
                f"[주문][R4][PERFORMANCE_HOLD] reason=FUNDING_RECONCILIATION_FAILED err={type(exc).__name__}",
                sec=60,
            )
            return {
                "complete":False,"reason":"FUNDING_RECONCILIATION_FAILED",
                "error_type":type(exc).__name__,"held_instances":sorted(target_ids),
            }
        if not result.get("complete"):
            _log(
                "warning","R4_PERFORMANCE_FUNDING_INCOMPLETE",
                "[주문][R4][PERFORMANCE_HOLD] reason=FUNDING_ATTRIBUTION_OR_COVERAGE_INCOMPLETE",
                sec=60,
            )
        return result

    def _performance_evaluation_allowed(
        self, instance_id, evidence_key, evaluation_interval, cooldown,
    ):
        evidence_key=str(evidence_key or "").strip()
        if not evidence_key:
            return False,"PERFORMANCE_EVIDENCE_KEY_MISSING"
        now=int(time.time())
        with closing(self.runtime.ledger.connect()) as con:
            previous_eval=con.execute(
                "SELECT MAX(ts) FROM r4_tier_performance_evaluations "
                "WHERE instance_id=? AND evidence_key=?",
                (instance_id,evidence_key),
            ).fetchone()[0]
            last_transition=con.execute(
                "SELECT MAX(ts) FROM r4_tier_events WHERE instance_id=?",
                (instance_id,),
            ).fetchone()[0]
        if previous_eval and now-int(previous_eval)<evaluation_interval:
            return False,"EVALUATION_INTERVAL_NOT_ELAPSED"
        if last_transition and now-int(last_transition)<cooldown:
            return False,"TIER_TRANSITION_COOLDOWN"
        return True,"READY"

    def _tier_cycle(self):
        controller=strategy_runtime.r4_tier_controller()
        if controller.get("enabled") is not True:
            return []
        performance_auto=controller.get("performance_auto") or {}
        performance_enabled=(
            isinstance(performance_auto,dict)
            and performance_auto.get("enabled") is True
            and performance_auto.get("apply_enabled") is True
        )
        funding_auto=controller.get("funding_auto") or {}
        active_rows=strategy_runtime.r4_strategy_instances()
        self._refresh_available_margin_evidence(active_rows)
        if performance_enabled:
            evidence_actions=self.runtime.recover_pending(evidence_only=True)
            if any(row.get("action")=="ECONOMIC_EVIDENCE_HOLD" for row in evidence_actions):
                _log("warning","R4_EXECUTION_EVIDENCE_INCOMPLETE",
                     "[주문][R4][PERFORMANCE_HOLD] reason=EXECUTION_EVIDENCE_INCOMPLETE",sec=300)
        funding_result=(
            self._sync_performance_funding(active_rows,funding_auto)
            if performance_enabled
            else {"complete":False,"reason":"PERFORMANCE_TIER_AUTOMATION_DISABLED"}
        )
        results=[]
        for instance_id,row in active_rows.items():
            if row.get("approved_for_server_release") is not True:
                continue
            tier=str(row.get("tier") or "").upper()
            if tier not in R4_LIVE_TIERS:
                continue
            self._ingest_config_evidence(instance_id,row)
            if row.get("entry_blocked") is True and row.get("rollback_requested_to_local") is True:
                if self._recover_transient_leg_management_safety_latch(instance_id,row,controller):
                    row=strategy_runtime.r4_strategy_instance(instance_id) or row
            if performance_enabled and (
                row.get("entry_blocked") is True
                or row.get("rollback_requested_to_local") is True
            ):
                results.append({
                    "instance_id":instance_id,"action":"HOLD",
                    "reason":"EXISTING_SAFETY_LOCK_PRESERVED","target_tier":tier,
                    "apply":{"changed":False,"reason":"EXISTING_SAFETY_LOCK_PRESERVED"},
                })
                continue
            if not performance_enabled and row.get("entry_blocked") is True and row.get("rollback_requested_to_local") is True:
                results.append({"instance_id":instance_id,"action":"HOLD","reason":"LOCAL_VERSION_ROLLBACK_REQUIRED"})
                continue
            decision=self.runtime.evaluate_tier(instance_id,controller)
            if not performance_enabled:
                result=self._apply_tier_decision(instance_id,decision)
                results.append({"instance_id":instance_id,**decision,"apply":result})
                continue
            if decision.get("entry_blocked") is True:
                reason=str(decision.get("reason") or "INDEPENDENT_SAFETY_BLOCK")
                self._persist_tier_state(
                    instance_id,tier,reason,entry_blocked=True,
                    rollback_requested=bool(decision.get("rollback_requested_to_local",False)),
                )
                results.append({
                    "instance_id":instance_id,**decision,"action":"HOLD",
                    "target_tier":tier,"reason":reason,
                    "apply":{"changed":False,"reason":"INDEPENDENT_SAFETY_BLOCK"},
                })
                continue
            held_instances=set(funding_result.get("held_instances") or [])
            if not funding_result.get("complete") and (
                not held_instances or instance_id in held_instances
            ):
                performance_decision=self.runtime.evaluate_performance_tier(instance_id)
                performance_decision.update({
                    "action":"HOLD","target_tier":tier,
                    "reason":str(funding_result.get("reason") or "FUNDING_EVIDENCE_INCOMPLETE"),
                })
                self.runtime.record_performance_tier_evaluation(instance_id,performance_decision)
                results.append({
                    "instance_id":instance_id,**performance_decision,
                    "apply":{"changed":False,"reason":performance_decision["reason"]},
                })
                continue
            try:
                evaluation_interval=int(performance_auto.get("evaluation_interval_seconds",300))
                cooldown=int(performance_auto.get("transition_cooldown_seconds",3600))
            except (TypeError,ValueError) as exc:
                raise RuntimeError("INVALID_PERFORMANCE_TIER_TIMING") from exc
            if evaluation_interval<=0 or cooldown<0:
                raise RuntimeError("INVALID_PERFORMANCE_TIER_TIMING")
            performance_decision=self.runtime.evaluate_performance_tier(instance_id)
            # 카나리 연패 금지는 설정에 영구 저장한다. 기존 LEG 관리는 중지하지 않는다.
            if performance_decision.get("entry_blocked") is True:
                reason=str(performance_decision.get("reason") or "PERFORMANCE_ENTRY_BLOCK")
                self._persist_tier_state(
                    instance_id,tier,reason,entry_blocked=True,rollback_requested=False,
                )
                self.runtime.record_performance_tier_evaluation(instance_id,performance_decision)
                _log(
                    "warning","PERFORMANCE_ENTRY_BLOCK:"+instance_id+":"+reason,
                    f"[주문][R4][ENTRY_BLOCK] instance={instance_id} reason={reason}",sec=300,
                )
                results.append({"instance_id":instance_id,**performance_decision,
                                "apply":{"changed":True,"entry_blocked":True,"reason":reason}})
                continue
            legacy_action=str(decision.get("action") or "HOLD").upper()
            performance_action=str(performance_decision.get("action") or "HOLD").upper()
            if legacy_action=="BLOCK" or (
                performance_action=="PROMOTE" and legacy_action!="PROMOTE"
            ):
                previous_key=str(performance_decision.get("evidence_key") or "")
                gate_reason=(
                    "EXISTING_TIER_SAFETY_CONTRACT_BLOCKED"
                    if legacy_action=="BLOCK"
                    else "POSITIVE_SCALE_EVIDENCE_NOT_PASSED"
                )
                performance_decision.update({
                    "action":"HOLD","target_tier":tier,"reason":gate_reason,
                    "scale_gate_action":legacy_action,
                    "evidence_key":"PERF_GATE_HOLD_"+_stable_hash({
                        "evidence_key":previous_key,"gate_action":legacy_action,
                        "gate_reason":str(decision.get("reason") or ""),
                    }),
                })
            evidence_key=str(performance_decision.get("evidence_key") or "")
            # 새로 확인된 3연패 강등은 승급 대기시간 때문에 지연시키지 않는다.
            if performance_action=="DEMOTE" and performance_decision.get("reason")=="THREE_CONSECUTIVE_NET_LOSSES":
                allowed,wait_reason=True,""
            else:
                allowed,wait_reason=self._performance_evaluation_allowed(
                    instance_id,evidence_key,evaluation_interval,cooldown,
                )
            if not allowed:
                results.append({
                    "instance_id":instance_id,"action":"HOLD",
                    "reason":wait_reason,"target_tier":tier,
                    "apply":{"changed":False,"reason":wait_reason},
                })
                continue
            performance_action=str(performance_decision.get("action") or "HOLD").upper()
            if (
                performance_action in {"PROMOTE","DEMOTE"}
                and evidence_key
                and self.runtime.ledger.has_performance_tier_evaluation(instance_id,evidence_key)
            ):
                performance_decision.update({
                    "action":"HOLD","reason":"SAME_EVIDENCE_ALREADY_EVALUATED",
                })
                result={"changed":False,"reason":"SAME_EVIDENCE_ALREADY_EVALUATED"}
            elif str(performance_decision.get("action") or "").upper() in {"PROMOTE","DEMOTE"}:
                result=self._apply_performance_tier_decision(instance_id,performance_decision)
                self.runtime.record_performance_tier_evaluation(instance_id,performance_decision)
            else:
                saved=self.runtime.record_performance_tier_evaluation(instance_id,performance_decision)
                reason=(
                    "SAME_EVIDENCE_ALREADY_EVALUATED"
                    if not saved.get("inserted")
                    else str(performance_decision.get("reason") or "NO_TIER_CHANGE")
                )
                if reason=="SAME_EVIDENCE_ALREADY_EVALUATED":
                    performance_decision.update({"action":"HOLD","reason":reason})
                result={"changed":False,"reason":reason}
            results.append({"instance_id":instance_id,**performance_decision,"apply":result})
        return results

    # ================= 진입 =================
    def _capacity(self, symbol, side, approved_units):
        """?? reconcile? basket + ?? Common-Risk ???? atomic capacity? ????."""
        symbol = str(symbol or "").upper()
        side = str(side or "").upper()
        approved_units = int(approved_units or 0)

        policy = strategy_runtime.get(
            "r4_runtime.capacity_policy",
            {},
        )

        policy = (
            policy
            if isinstance(policy, dict)
            else {}
        )

        if (
            str(
                policy.get("mode")
                or ""
            ).upper()
            != "LIVE_RISK_APPROVED_INCREMENT"
            or policy.get("static_symbol_map") is not False
            or str(
                policy.get("unit")
                or ""
            ).upper()
            != "EXCHANGE_LOT_SIZE_STEP_INTEGER_UNITS"
            or str(
                policy.get("universe_source")
                or ""
            ).upper()
            != "CURRENT_ACTIVE_CONFIG"
        ):
            raise RuntimeError(
                "R4_DYNAMIC_CAPACITY_POLICY_REQUIRED"
            )

        if (
            side not in ("LONG", "SHORT")
            or approved_units <= 0
        ):
            raise RuntimeError(
                "R4_DYNAMIC_CAPACITY_INPUT_INVALID:"
                + symbol
            )

        legs = self.runtime.ledger.active_legs(
            symbol=symbol,
            side=side,
        )

        owned = sum(
            int(
                row.get("owned_qty_units")
                or 0
            )
            for row in legs
        )

        snapshot = (
            self.runtime.ledger
            .basket_snapshot(
                symbol,
                side,
            )
        )

        if snapshot:
            ledger_units = int(
                snapshot.get(
                    "ledger_qty_units"
                )
                or 0
            )

            exchange_units = int(
                snapshot.get(
                    "exchange_qty_units"
                )
                or 0
            )

            mismatch = int(
                snapshot.get("mismatch")
                or 0
            )

            if (
                mismatch
                or ledger_units != owned
                or exchange_units != owned
            ):
                raise RuntimeError(
                    "R4_CAPACITY_RECONCILE_REQUIRED:"
                    + symbol
                    + ":"
                    + side
                )

        elif owned != 0:
            raise RuntimeError(
                "R4_CAPACITY_RECONCILE_REQUIRED:"
                + symbol
                + ":"
                + side
            )

        # ?? ?? ?? owned + ?? Risk ?? increment.
        # ?? ACTIVE reservation? ??? ledger? atomic ?? ????.
        return owned + approved_units

    def _matches_result(self, instance, result):
        frame = str(result.get("frame") or "").upper()
        if str(instance.get("timeframe") or "").upper() != frame:
            return False
        instance_id = str(instance.get("strategy_instance_id") or "")
        if str(result.get("strategy_instance_id") or "") != instance_id:
            return False
        if str(instance.get("exact_strategy_version") or "") != str(result.get("strategy_version") or ""):
            return False
        strategy_id = str(instance.get("strategy_id") or "").upper()
        direct = str(result.get("strategy") or "").upper()
        components = {str(x).upper() for x in (result.get("strategies") or [])}
        return strategy_id == direct or strategy_id in components

    def _r4_common_leverage(self, symbol):
        """연구 기본값에 사용자 증분을 더하되 기존 바스켓·거래소 상한을 보존한다."""
        symbol = str(symbol or "").upper()

        common = strategy_runtime.get(
            "r4_runtime.common_leverage_policy",
            {},
        )

        common = (
            common
            if isinstance(common, dict)
            else {}
        )

        if (
            str(
                common.get("mode")
                or ""
            ).upper()
            != "MIN_RESEARCH_APPROVED_BASELINE"
        ):
            return 0

        if (
            str(
                common.get("source")
                or ""
            )
            != "R5A_LEVERAGE_FROZEN_STANDARD_V2"
        ):
            return 0

        if (
            str(
                common.get("symbol_scope")
                or ""
            ).upper()
            != "DYNAMIC_UNIVERSE"
        ):
            return 0

        if (
            str(
                common.get("open_basket_change")
                or ""
            ).upper()
            != "DEFER_UNTIL_FLAT"
        ):
            return 0

        approved = (
            common.get("approved_sources")
            or {}
        )

        if not isinstance(
            approved,
            dict,
        ):
            return 0

        try:
            normalized_sources = {
                str(k).upper():
                    int(float(v))
                for k, v
                in approved.items()
            }

            requested = int(
                float(
                    common.get("target")
                    or 0
                )
            )

        except Exception:
            return 0

        # Frozen R5A evidence exact:
        # 12H = 10x
        # 1D  = 15x
        expected_sources = {
            "12H": 10,
            "1D": 15,
        }

        if (
            normalized_sources
            != expected_sources
            or requested != 10
            or requested
            != min(
                expected_sources.values()
            )
        ):
            return 0

        increment = common.get("owner_leverage_increment_x", 0)
        if type(increment) is not int or increment not in (0, 5, 10, 15, 20):
            return 0
        requested += increment

        policy = strategy_runtime.get(
            "leverage_policy",
            {},
        )

        policy = (
            policy
            if isinstance(policy, dict)
            else {}
        )

        try:
            minimum = int(
                float(
                    policy.get(
                        "min",
                        1,
                    )
                    or 1
                )
            )

            policy_max = int(
                float(
                    policy.get(
                        "absolute_max",
                        0,
                    )
                    or 0
                )
            )

        except Exception:
            return 0

        if (
            requested
            < max(1, minimum)
            or policy_max < 1
        ):
            return 0

        exchange_max = (
            frozen_kernel
            .exchange_max_leverage(
                symbol
            )
        )

        if exchange_max < 1:
            return 0

        actual = min(
            requested,
            policy_max,
            exchange_max,
        )

        if actual < max(
            1,
            minimum,
        ):
            return 0

        return int(actual)


    def _r4_risk_entry_guard(
        self,
        symbol,
        side,
        qty,
        price,
        frame,
        strategy,
    ):
        """
        ?? ??? Risk Engine? ??? ?????.
        legacy runner? same-symbol ???? ???? ???.
        """
        symbol = frozen_kernel.u(symbol)
        side = frozen_kernel.norm_side(side)

        requested_qty = frozen_kernel.f(
            qty,
            0.0,
        )

        if requested_qty <= 0:
            return {
                "allow": False,
                "reason": "QTY_INVALID",
                "post": {},
                "approved_qty": 0.0,
            }

        if not frozen_kernel.strategy_feature_enabled(
            "entry_gate",
            True,
        ):
            return {
                "allow": True,
                "reason":
                    "ENTRY_GATE_DISABLED_BY_CONFIG",
                "post": {},
                "approved_qty":
                    requested_qty,
                "requested_qty":
                    requested_qty,
            }

        try:
            positions = (
                frozen_kernel.risk_live_position_book()
            )

            wallet = (
                frozen_kernel.risk_wallet_balance()
            )

            marks = frozen_kernel.risk_mark_map(
                positions,
                {
                    symbol: price,
                },
            )

            brackets = (
                frozen_kernel.risk_leverage_brackets(
                    set(positions)
                    | {symbol}
                )
            )

            decision_ts = (
                frozen_kernel.now_ts()
            )

            def evaluate(test_qty):
                candidate = {
                    "symbol": symbol,

                    "side":
                        1
                        if side == "BUY"
                        else -1,

                    "entry_price":
                        float(price),

                    "qty":
                        float(test_qty),

                    "entry_ts":
                        int(decision_ts),

                    "owner_tf":
                        frozen_kernel.u(frame),

                    "owner_strategy":
                        frozen_kernel.u(strategy),
                }

                return (
                    frozen_kernel.risk_engine.entry_gate(
                        wallet=wallet,
                        positions=positions,
                        candidate=candidate,
                        marks=marks,
                        leverage_brackets=brackets,
                        decision_ts=decision_ts,
                    )
                )

            first = evaluate(
                requested_qty
            )

            if not isinstance(first, dict):
                raise RuntimeError(
                    "RISK_GATE_RESULT_INVALID"
                )

            first["wallet"] = float(wallet)

            first["decision_ts"] = int(
                decision_ts
            )

            first[
                "requested_qty"
            ] = requested_qty

            if bool(first.get("allow")):
                first[
                    "approved_qty"
                ] = requested_qty

                first[
                    "v3_entry_risk"
                ] = (
                    frozen_kernel.risk_engine
                    .v3_entry_risk_contract(
                        first,
                        candidate={
                            "qty":
                                requested_qty,
                            "entry_price":
                                price,
                        },
                    )
                )

                first[
                    "v3_entry_risk"
                ][
                    "safe_size_qty"
                ] = requested_qty

                return first

            if (
                frozen_kernel.s(
                    first.get("reason")
                )
                != "STRESS_BREACH"
            ):
                first[
                    "approved_qty"
                ] = 0.0
                return first

            lo = 0.0
            hi = requested_qty
            best = None

            for _ in range(12):
                mid = (
                    lo + hi
                ) / 2.0

                if mid <= 0:
                    break

                row = evaluate(mid)

                if (
                    isinstance(row, dict)
                    and bool(row.get("allow"))
                ):
                    lo = mid
                    best = row
                else:
                    hi = mid

            if best is None or lo <= 0:
                first[
                    "approved_qty"
                ] = 0.0
                return first

            best["wallet"] = float(wallet)

            best[
                "decision_ts"
            ] = int(decision_ts)

            best[
                "requested_qty"
            ] = requested_qty

            best[
                "approved_qty"
            ] = float(lo)

            best[
                "reason"
            ] = (
                "REDUCE_TO_CROSS_STRESS_SAFE_SIZE"
            )

            best[
                "v3_entry_risk"
            ] = (
                frozen_kernel.risk_engine
                .v3_entry_risk_contract(
                    best,
                    candidate={
                        "qty": lo,
                        "entry_price": price,
                    },
                )
            )

            best[
                "v3_entry_risk"
            ].update({
                "decision": "REDUCE",
                "safe_size_qty":
                    float(lo),
                "requested_qty":
                    requested_qty,
            })

            return best

        except Exception as error:
            return {
                "allow": False,
                "reason": "RISK_EXCEPTION",
                "error":
                    f"{type(error).__name__}:"
                    f"{str(error)[:200]}",
                "post": {},
                "approved_qty": 0.0,
            }

    def _plan(self, symbol, result, instance, *, state_reentry=False):
        """?? V3 Late Entry/????? ????? ??? R4 ????? ???."""
        symbol = str(symbol or "").upper()
        frame = str(
            instance.get("timeframe")
            or result.get("frame")
            or ""
        ).upper()

        strategy = str(
            instance.get("strategy_id")
            or result.get("strategy")
            or ""
        ).upper()

        exact_version = str(
            instance.get("exact_strategy_version")
            or ""
        )

        # ====================================================
        # R4_ENTRY_BLOCK_CACHE_V1
        # ?? signal? ?? Late Entry BLOCK?
        # ?? confirmed 1H boundary?? ???/API ?? ??.
        # ====================================================

        _entry_block_cache = getattr(
            self,
            "_entry_block_cache",
            None,
        )

        if not isinstance(
            _entry_block_cache,
            dict,
        ):
            _entry_block_cache = {}
            self._entry_block_cache = _entry_block_cache

        _raw_cache_candidate = (
            result.get("_v3_candidate")
            if isinstance(
                result.get("_v3_candidate"),
                dict,
            )
            else {}
        )

        _raw_cache_signal = (
            _raw_cache_candidate.get("signal")
            if isinstance(
                _raw_cache_candidate.get("signal"),
                dict,
            )
            else {}
        )

        _cache_signal_time = (
            _raw_cache_signal.get("signal_time")
            or result.get("signal_time")
            or result.get("confirmed_open_time")
            or result.get("signal_open_time")
            or ""
        )

        _entry_block_cache_key = (
            (
                symbol,
                frame,
                strategy,
                exact_version,
                str(_cache_signal_time),
            )
            if (
                not state_reentry
                and _cache_signal_time
            )
            else None
        )

        if _entry_block_cache_key is not None:
            import time as _entry_cache_time

            _cached_block = _entry_block_cache.get(
                _entry_block_cache_key
            )

            if isinstance(_cached_block, dict):
                _cache_now = float(
                    _entry_cache_time.time()
                )

                _cache_expiry = float(
                    _cached_block.get("expires_at")
                    or 0.0
                )

                if _cache_now < _cache_expiry:
                    _log(
                        "info",
                        (
                            f"R4_ENTRY_BLOCK_CACHE:"
                            f"{symbol}:"
                            f"{frame}:"
                            f"{strategy}"
                        ),
                        (
                            "[??][R4][ENTRY_BLOCK_CACHE_HIT] "
                            f"symbol={symbol} "
                            f"frame={frame} "
                            f"strategy={strategy} "
                            f"reason="
                            f"{_cached_block.get('reason')} "
                            f"signal_time="
                            f"{_cache_signal_time} "
                            f"expires_at="
                            f"{_cache_expiry:.0f}"
                        ),
                        sec=60,
                    )

                    return None

                _entry_block_cache.pop(
                    _entry_block_cache_key,
                    None,
                )

        price = frozen_kernel.get_price(symbol)

        if (
            price <= 0
            or not frame
            or not strategy
            or not exact_version
        ):
            return None

        # ====================================================
        # Strategy Instance Tier ceiling
        # ====================================================

        tier = str(
            instance.get("tier")
            or ""
        ).upper()

        tier_weight, tier_weight_meta = _r4_instance_entry_capital_weight(
            instance,
            tier,
        )

        if tier_weight <= 0:
            _log(
                "info",
                f"R4_CAPITAL_WEIGHT:{instance.get('strategy_instance_id')}:{tier}",
                (
                    "[R4][ENTRY_BLOCK] reason=INVALID_STRATEGY_INSTANCE_CAPITAL_WEIGHT "
                    f"tier={tier} source={tier_weight_meta.get('source')}"
                ),
                sec=60,
            )
            return None

        # ====================================================
        # ?? ????
        # ====================================================

        allocation_meta = {
            "decision": "ALLOW",
            "reason": "R4_STATE_REENTRY_COMPAT",
            "evidence_weight": 1.0,
        }

        allocation_weight = 1.0

        if not state_reentry:

            candidate = (
                result.get("_v3_candidate")
                if isinstance(
                    result.get("_v3_candidate"),
                    dict,
                )
                else None
            )

            if candidate is None:

                candidate_input = dict(result)

                candidate_input[
                    "frame"
                ] = frame

                candidate_input[
                    "strategy"
                ] = strategy

                candidate_input[
                    "strategy_version"
                ] = exact_version

                candidate = candidate_contract.build(
                    candidate_input,
                    signal_price=
                        result.get(
                            "signal_price"
                        ),
                    current_price=price,
                )

            else:

                candidate = dict(candidate)

                candidate[
                    "timeframe"
                ] = frame

                candidate[
                    "strategy_id"
                ] = strategy

                candidate[
                    "strategy_version"
                ] = exact_version

                signal = dict(
                    candidate.get("signal")
                    or {}
                )

                signal[
                    "current_price"
                ] = float(price)

                candidate[
                    "signal"
                ] = signal

            candidate = candidate_contract.bind_knowledge(candidate)

            # ================================================
            # Late Entry
            # ================================================

            replay = late_entry.replay(
                candidate,
                current_price=price,
            )

            candidate[
                "late_entry"
            ] = replay

            if (
                str(
                    replay.get("decision")
                    or "BLOCK"
                ).upper()
                != "ALLOW"
            ):
                _log(
                    "info",
                    (
                        f"R4_LATE_ENTRY:"
                        f"{symbol}:"
                        f"{frame}:"
                        f"{strategy}:"
                        f"{replay.get('reason')}"
                    ),
                    (
                        "[??][R4][ENTRY_BLOCK] "
                        "reason=LATE_ENTRY "
                        f"symbol={symbol} "
                        f"frame={frame} "
                        f"strategy={strategy} "
                        f"detail={replay.get('reason')} "
                        f"status="
                        f"{replay.get('replay_status')}"
                    ),
                    sec=60,
                )

                # R4_ENTRY_BLOCK_CACHE_STORE_V1
                _replay_reason = str(
                    replay.get("reason")
                    or ""
                ).upper()

                if (
                    _entry_block_cache_key is not None
                    and _replay_reason
                    in {
                        "INTRABAR_ORDER_AMBIGUOUS",
                        "POSITION_NOT_ALIVE_AT_NOW",
                    }
                ):
                    import time as _entry_cache_time

                    _cache_now = float(
                        _entry_cache_time.time()
                    )

                    # UTC epoch ?? ?? ?? 1H + 5?.
                    # ? confirmed 1H ???? ??? ???.
                    _cache_expiry = (
                        (
                            int(_cache_now)
                            // 3600
                            + 1
                        )
                        * 3600
                        + 5
                    )

                    _entry_block_cache[
                        _entry_block_cache_key
                    ] = {
                        "reason": _replay_reason,
                        "expires_at": float(
                            _cache_expiry
                        ),
                        "signal_time": str(
                            _cache_signal_time
                        ),
                    }

                return None

            # ================================================
            # Expected Account Contribution
            #
            # deployment_weight? ??? ?? ???? ???.
            # R4??? Strategy Instance tier_weight? authority?.
            # ================================================

            allocation_meta = capital_allocator.evidence_priority_weight(
                candidate,
                max(
                    1,
                    len(
                        _symbols(
                            self.cfg
                        )
                    ),
                ),
            )

            try:
                allocation_weight = float(
                    allocation_meta.get(
                        "weight"
                    )
                    or 0.0
                )
            except Exception:
                allocation_weight = 0.0

            if (
                allocation_meta.get(
                    "decision"
                )
                != "ALLOW"
                or allocation_weight <= 0
            ):
                _log(
                    "info",
                    (
                        f"R4_ALLOC:"
                        f"{symbol}:"
                        f"{frame}:"
                        f"{strategy}:"
                        f"{allocation_meta.get('reason')}"
                    ),
                    (
                        "[??][R4][ENTRY_BLOCK] "
                        "reason=CAPITAL_ALLOCATION "
                        f"symbol={symbol} "
                        f"frame={frame} "
                        f"strategy={strategy} "
                        f"detail="
                        f"{allocation_meta.get('reason')}"
                    ),
                    sec=60,
                )

                return None

        # ====================================================
        # Evidence allocation ? exact Strategy Instance tier
        # ====================================================

        final_weight = allocation_weight * float(tier_weight)

        if final_weight <= 0:
            return None

        allocation_meta = dict(
            allocation_meta
        )

        allocation_meta.update({
            "tier":
                tier,

            "tier_weight":
                float(
                    tier_weight
                ),

            "base_tier_weight":
                float(tier_weight_meta.get("base_tier_weight") or 0.0),

            "capital_weight_source":
                str(tier_weight_meta.get("source") or ""),

            "adaptive_capital_boost_multiplier":
                tier_weight_meta.get("boost_multiplier"),

            "evidence_weight":
                float(
                    allocation_weight
                ),

            "final_weight":
                float(
                    final_weight
                ),
        })

        # ====================================================
        # Common Symbol Leverage
        # ====================================================

        leverage = self._r4_common_leverage(
            symbol
        )

        if leverage <= 0:
            return None

        # ====================================================
        # Frozen order sizing
        # ====================================================

        plan = frozen_kernel.load_entry_plan(
            symbol,
            result["side"],
            leverage,
            weight=final_weight,
        )

        if not isinstance(
            plan,
            dict,
        ):
            return None

        # ====================================================
        # Tier Capital Ceiling
        # ====================================================

        try:
            alloc_usdt = float(
                plan.get(
                    "alloc_usdt"
                )
                or 0.0
            )

            planned_notional = float(
                plan.get(
                    "notional"
                )
                or 0.0
            )

            capital_ceiling = (
                alloc_usdt
                * float(
                    leverage
                )
            )

        except Exception:
            return None

        tolerance = max(
            1e-9,
            abs(
                capital_ceiling
            )
            * 1e-9,
        )

        # CANARY_MIN_ORDER_NOTIONAL_GRACE_V1
        # ??? ???? ???? 6 USDT? ? ???
        # CANARY tier ceiling? ?? ??? ??.
        if (
            alloc_usdt <= 0
            or planned_notional <= 0
            or (
                planned_notional
                > capital_ceiling
                + tolerance
                and not (
                    tier == "CANARY"
                    and planned_notional
                    <= 6.0 + tolerance
                )
            )
        ):
            _log(
                "info",
                f"R4_TIER_CAP:{symbol}:{tier}",
                (
                    "[??][R4][ENTRY_BLOCK] "
                    "reason="
                    "TIER_CAPITAL_CEILING_EXCEEDED "
                    f"symbol={symbol} "
                    f"tier={tier} "
                    f"planned="
                    f"{planned_notional:.12g} "
                    f"ceiling="
                    f"{capital_ceiling:.12g}"
                ),
                sec=60,
            )

            return None

        # ====================================================
        # LOT_SIZE
        # ====================================================

        ok, _, qty = frozen_kernel.validate_qty(
            symbol,
            plan.get("qty"),
            price,
        )

        if not ok:
            return None

        # ====================================================
        # R4 same-symbol compatible Common Risk
        # ====================================================

        risk = self._r4_risk_entry_guard(
            symbol,
            result["side"],
            qty,
            price,
            frame,
            strategy,
        )

        if (
            not isinstance(
                risk,
                dict,
            )
            or not risk.get(
                "allow"
            )
        ):
            return None

        approved = min(
            float(qty),
            float(
                risk.get(
                    "approved_qty"
                )
                or qty
            ),
        )

        units = self.exchange.qty_to_units(
            symbol,
            approved,
        )

        if units <= 0:
            return None

        return (
            units,
            leverage,
            price,
            risk,
            allocation_meta,
        )

    def _record_explicit_risk_evidence(self, instance_id, risk):
        """risk 결과 안에 실제로 존재하는 헌법 evidence key만 기록한다."""
        if not isinstance(risk, dict):
            return
        sources = [risk]
        nested = risk.get("v3_entry_risk")
        if isinstance(nested, dict):
            sources.append(nested)
        for name in strategy_runtime.r4_required_scale_evidence():
            if name in {"EXACT_STRATEGY_INSTANCE_LIFECYCLE", "RELATIVE_TIER_COMPETITION_PASS", "STRATEGY_LEG_LEDGER_RECONCILE_PASS", "NO_CRITICAL_HARD_FAIL"}:
                continue
            candidates = (name, name.lower())
            found = None
            for source in sources:
                for key in candidates:
                    if key in source and isinstance(source.get(key), bool):
                        found = source.get(key)
                        break
                if found is not None:
                    break
            if found is None:
                continue
            self.runtime.record_scale_evidence(
                instance_id,
                name,
                found,
                {"source": "RUNTIME_RISK_RESULT"},
            )

    # ================= Leg 자동 운영 =================
    def _leg_behavior_policy(self, leg_snapshot):
        """open leg가 entry 시점에 가진 exact Policy Bundle만 해석한다."""
        root = leg_snapshot.get("policy_snapshot_obj") if isinstance(leg_snapshot, dict) else {}
        root = root if isinstance(root, dict) else {}
        immutable = root.get("policy_snapshot") if isinstance(root.get("policy_snapshot"), dict) else root
        versions = immutable.get("policy_versions") if isinstance(immutable.get("policy_versions"), dict) else {}
        behavior = immutable.get("behavior_policy") if isinstance(immutable.get("behavior_policy"), dict) else immutable
        return behavior if isinstance(behavior, dict) else {}, versions

    def _manage_active_legs(self, blocked_pairs=None):
        """정합성 검증된 Leg만 불변 EXIT/TRAILING 정책으로 관리한다."""
        blocked_pairs = set(blocked_pairs or ())
        touched = set()
        for leg in self.runtime.ledger.active_legs():
            leg_id = str(leg.get("leg_id") or "")
            instance_id = str(leg.get("instance_id") or "")
            symbol = str(leg.get("symbol") or "").upper()
            side = str(leg.get("position_side") or "").upper()
            if not leg_id or not instance_id or not symbol or side not in {"LONG", "SHORT"}:
                continue
            # 거래소와 수량이 불일치한 바스켓은 안전하게 보류한다.
            # 정상 바스켓의 기존 포지션 익절은 계속 관리한다.
            if (symbol, side) in blocked_pairs:
                continue
            try:
                snap = self.runtime.ledger.leg_management_snapshot(leg_id)
                # 아직 entry order 자체가 미종결이면 exit와 entry가 교차하지 않게 기다린다.
                if str(snap.get("entry_intent_status") or "") not in {"FILLED", "CANCELLED"}:
                    continue
                if int(snap.get("owned_qty_units") or 0) <= 0 or int(snap.get("pending_exit_qty_units") or 0) > 0:
                    continue

                behavior, versions = self._leg_behavior_policy(snap)
                exit_version = str(versions.get("EXIT") or "").strip()
                if not exit_version or exit_version != str(snap.get("trailing_policy_version") or ""):
                    raise RuntimeError("LEG_EXIT_POLICY_VERSION_MISMATCH")

                mark_price = frozen_kernel.get_price(symbol)
                if mark_price <= 0:
                    continue
                mark_ticks = self.exchange.price_to_ticks(symbol, mark_price)
                entry_ticks = int(snap.get("entry_vwap_ticks") or 0)
                if entry_ticks <= 0 or mark_ticks <= 0:
                    raise RuntimeError("LEG_ENTRY_OR_MARK_PRICE_TICKS_MISSING")

                # ---------- Leg별 MFE/MAE 관측 + trailing ----------
                trailing = behavior.get("trailing") if isinstance(behavior.get("trailing"), dict) else {}
                state = snap.get("trailing_state") if isinstance(snap.get("trailing_state"), dict) else {}
                state_policy = str(state.get("policy_version") or "")
                if state_policy and state_policy != exit_version:
                    raise RuntimeError("OPEN_LEG_TRAILING_POLICY_MUTATION_FORBIDDEN")
                if int(state.get("entry_price_ticks") or 0) not in (0, entry_ticks):
                    raise RuntimeError("OPEN_LEG_ENTRY_PRICE_MUTATION_FORBIDDEN")

                # 15초 Runtime loop에서 같은 Leg row의 extrema만 갱신한다.
                # 이벤트 row를 틱마다 추가하지 않아 DB 폭증을 막는다.
                if side == "LONG":
                    observed_mfe = max(0, mark_ticks - entry_ticks)
                    observed_mae = max(0, entry_ticks - mark_ticks)
                else:
                    observed_mfe = max(0, entry_ticks - mark_ticks)
                    observed_mae = max(0, mark_ticks - entry_ticks)
                mfe_ticks = max(int(state.get("mfe_ticks") or 0), observed_mfe)
                mae_ticks = max(int(state.get("mae_ticks") or 0), observed_mae)

                activated = bool(int(state.get("activated") or 0))
                extreme = int(state.get("extreme_ticks") or mark_ticks)
                trigger = int(state.get("trigger_ticks") or 0)
                changed = (
                    not state
                    or mfe_ticks != int(state.get("mfe_ticks") or 0)
                    or mae_ticks != int(state.get("mae_ticks") or 0)
                )
                trail_hit = False

                if trailing.get("enabled", True) is True:
                    if trailing.get("dynamic_adjustment") is True:
                        raise RuntimeError("OPEN_LEG_DYNAMIC_TRAILING_FORBIDDEN")
                    start_pct = Decimal(str(trailing.get("start_pct") or "0"))
                    callback_pct = Decimal(str(trailing.get("callback_pct") or "0"))
                    if start_pct <= 0 or callback_pct <= 0:
                        raise RuntimeError("LEG_TRAILING_POLICY_INVALID")

                    move_pct = (
                        (Decimal(mark_ticks - entry_ticks) * Decimal("100") / Decimal(entry_ticks))
                        if side == "LONG"
                        else (Decimal(entry_ticks - mark_ticks) * Decimal("100") / Decimal(entry_ticks))
                    )
                    if not activated and move_pct >= start_pct:
                        activated = True
                        extreme = mark_ticks
                        changed = True

                    if activated:
                        old_extreme = extreme
                        if side == "LONG":
                            extreme = max(extreme, mark_ticks)
                            trigger = int(
                                (Decimal(extreme) * (Decimal("100") - callback_pct) / Decimal("100"))
                                .to_integral_value(rounding=ROUND_DOWN)
                            )
                            trail_hit = mark_ticks <= trigger
                        else:
                            extreme = min(extreme, mark_ticks)
                            trigger = int(
                                (Decimal(extreme) * (Decimal("100") + callback_pct) / Decimal("100"))
                                .to_integral_value(rounding=ROUND_UP)
                            )
                            trail_hit = mark_ticks >= trigger
                        changed = changed or extreme != old_extreme or trigger != int(state.get("trigger_ticks") or 0)

                if changed:
                    self.runtime.ledger.update_trailing(
                        leg_id,
                        entry_ticks,
                        mark_ticks,
                        extreme,
                        trigger,
                        activated,
                        exit_version,
                        mfe_ticks,
                        mae_ticks,
                    )

                if trail_hit:
                    exit_signal_id = f"TRAIL_EXIT:{leg_id}:{exit_version}"
                    result = self.runtime.exit_leg(
                        leg_id,
                        int(snap["owned_qty_units"]),
                        exit_signal_id,
                        reason=f"TRAILING:{exit_version}",
                    )
                    touched.add((instance_id, symbol))
                    _log(
                        "info",
                        f"TRAIL_EXIT:{leg_id}:{result.get('status')}",
                        f"[주문][R4][TRAIL_EXIT] leg={leg_id} instance={instance_id} symbol={symbol} side={side} status={result.get('status')}",
                        sec=300,
                    )
                    continue

                # ---------- exact Strategy Instance 반대 신호 Exit ----------
                live_instance = strategy_runtime.r4_strategy_instance(instance_id)
                live_frame = strategy_runtime.frame(snap["frame"])
                if (
                    not live_instance
                    or str(live_instance.get("exact_strategy_version") or "") != str(snap.get("strategy_version") or "")
                ):
                    raise RuntimeError("EXACT_INSTANCE_EXIT_EVALUATOR_UNAVAILABLE")
                opposite = behavior.get("opposite_exit") if isinstance(behavior.get("opposite_exit"), dict) else {}
                if opposite.get("enabled", True) is not True:
                    continue
                if str(opposite.get("mode") or "STANDARD_COMBINED").upper() != "STANDARD_COMBINED":
                    raise RuntimeError("UNSUPPORTED_EXACT_EXIT_MODE")
                decision = gate.evaluate_owned_position(
                    symbol, snap["frame"], snap["strategy_id"], state_mode=False,
                    strategy_instance_id=instance_id, exact_strategy_version=snap["strategy_version"],
                )
                decision_side = "LONG" if str(decision.get("side") or "").upper() == "BUY" else "SHORT" if str(decision.get("side") or "").upper() == "SELL" else ""
                if int(decision.get("allow") or 0) != 1 or not decision_side or decision_side == side:
                    continue
                signal = str(decision.get("signal_id") or "")
                if not signal:
                    raise RuntimeError("OWNED_EXIT_SIGNAL_ID_MISSING")
                exit_signal_id = "OPPOSITE_EXIT:" + _stable_hash({"leg_id": leg_id, "signal_id": signal})[:32]
                result = self.runtime.exit_leg(
                    leg_id,
                    int(snap["owned_qty_units"]),
                    exit_signal_id,
                    reason=f"OPPOSITE_EXIT_TO_{decision_side}:{exit_version}",
                )
                touched.add((instance_id, symbol))
                _log(
                    "info",
                    f"OPPOSITE_EXIT:{leg_id}:{signal}:{result.get('status')}",
                    f"[주문][R4][STRATEGY_EXIT] leg={leg_id} instance={instance_id} symbol={symbol} old={side} target={decision_side} status={result.get('status')}",
                    sec=300,
                )
            except Exception as exc:
                touched.add((instance_id, symbol))
                self.runtime.disable_entry_admission("LEG_MANAGEMENT_HARD_FAIL")
                self._record_server_owned_boolean_evidence(
                    instance_id,
                    "NO_CRITICAL_HARD_FAIL",
                    False,
                    {"source": "LEG_MANAGEMENT", "leg_id": leg_id, "error": type(exc).__name__},
                )
                _log(
                    "error",
                    f"LEG_MANAGE_FAIL:{leg_id}:{type(exc).__name__}",
                    f"[주문][R4][LEG_MANAGE_FAIL] leg={leg_id} instance={instance_id} symbol={symbol} err={type(exc).__name__}:{exc}",
                    sec=60,
                )
        return touched

    def _manage_reentries(self, blocked_pairs):
        """완전히 닫힌 source Leg에서만 다음 1H current-state를 확인해 새 LEG_ID를 만든다."""
        blocked_pairs = set(blocked_pairs or set())
        created = set()
        now = int(time.time())
        rows = strategy_runtime.r4_strategy_instances()
        for source in self.runtime.ledger.closed_reentry_candidates():
            source_leg_id = str(source.get("leg_id") or "")
            instance_id = str(source.get("instance_id") or "")
            symbol = str(source.get("symbol") or "").upper()

            if symbol not in set(_symbols(self.cfg)):
                self.runtime.ledger.record_reentry_cancelled(
                    source_leg_id,
                    "SYMBOL_NOT_IN_CURRENT_ACTIVE_UNIVERSE",
                )
                continue

            if (instance_id, symbol) in blocked_pairs:
                continue
            instance = rows.get(instance_id) if isinstance(rows.get(instance_id), dict) else {}
            if (
                not instance
                or instance.get("approved_for_server_release") is not True
                or instance.get("enabled") is not True
                or instance.get("entry_enabled", True) is not True
                or instance.get("entry_blocked") is True
            ):
                self.runtime.ledger.record_reentry_cancelled(source_leg_id, "SOURCE_INSTANCE_NOT_ENTRY_ENABLED")
                continue

            behavior, versions = self._leg_behavior_policy(source)
            reentry_version = str(versions.get("REENTRY") or "").strip()
            if not reentry_version:
                self.runtime.ledger.record_reentry_cancelled(source_leg_id, "REENTRY_POLICY_VERSION_MISSING")
                continue
            exit_reason = str((source.get("exit_detail") or {}).get("reason") or "")
            if exit_reason.startswith("TRAILING:"):
                policy = behavior.get("state_reentry") if isinstance(behavior.get("state_reentry"), dict) else {}
                target_side = str(source.get("position_side") or "").upper()
                allowed_modes = {"CURRENT_STATE"}
            elif exit_reason.startswith("OPPOSITE_EXIT_TO_"):
                policy = behavior.get("opposite_reopen") if isinstance(behavior.get("opposite_reopen"), dict) else {}
                target_side = exit_reason.split(":", 1)[0].replace("OPPOSITE_EXIT_TO_", "").upper()
                allowed_modes = {"NEXT_1H_STATE"}
            else:
                self.runtime.ledger.record_reentry_cancelled(source_leg_id, "EXIT_REASON_HAS_NO_REENTRY_CONTRACT")
                continue

            if policy.get("enabled", True) is not True:
                self.runtime.ledger.record_reentry_cancelled(source_leg_id, "REENTRY_DISABLED_BY_FROZEN_POLICY")
                continue
            mode = str(policy.get("mode") or "").upper()
            if mode not in allowed_modes:
                self.runtime.ledger.record_reentry_cancelled(source_leg_id, "REENTRY_MODE_NOT_SUPPORTED", {"mode": mode})
                continue

            trailing = behavior.get("trailing") if isinstance(behavior.get("trailing"), dict) else {}
            try:
                retention = int(trailing.get("state_retention_sec") or 0)
            except Exception:
                retention = 0
            exit_ts = int(source.get("exit_ts") or 0)
            if retention <= 0 or exit_ts <= 0:
                self.runtime.ledger.record_reentry_cancelled(source_leg_id, "REENTRY_RETENTION_CONTRACT_INVALID")
                continue
            if now - exit_ts > retention:
                self.runtime.ledger.record_reentry_cancelled(source_leg_id, "REENTRY_STATE_EXPIRED")
                continue
            individual_mode = str(policy.get("individual_mode") or "").upper()
            if individual_mode == "COOLDOWN_BARS":
                # Compatibility only for already-frozen Strategy Instances created
                # before STAGE13. New research/deployment may not bind this mode.
                try:
                    cooldown_bars = int(policy.get("cooldown_bars"))
                    earliest_reentry_ts = _r4_reentry_earliest_ts(
                        exit_ts,
                        str(source.get("frame") or instance.get("timeframe") or ""),
                        cooldown_bars,
                    )
                except Exception:
                    self.runtime.ledger.record_reentry_cancelled(
                        source_leg_id,
                        "REENTRY_COOLDOWN_CONTRACT_INVALID",
                    )
                    continue
                if now < earliest_reentry_ts:
                    continue
            elif individual_mode in {"", "CURRENT_STATE_NEXT_1H"}:
                if str(policy.get("next_check_tf") or "1H").upper() != "1H":
                    self.runtime.ledger.record_reentry_cancelled(
                        source_leg_id,
                        "REENTRY_NEXT_CHECK_TF_INVALID",
                    )
                    continue
                next_hour = ((exit_ts // 3600) + 1) * 3600
                if now < next_hour:
                    continue
            else:
                self.runtime.ledger.record_reentry_cancelled(
                    source_leg_id,
                    "REENTRY_INDIVIDUAL_MODE_NOT_SUPPORTED",
                    {"individual_mode": individual_mode},
                )
                continue

            frame_row = strategy_runtime.frame(source["frame"])
            if (
                str(instance.get("exact_strategy_version") or "") != str(source.get("strategy_version") or "")
            ):
                self._record_server_owned_boolean_evidence(
                    instance_id,
                    "NO_CRITICAL_HARD_FAIL",
                    False,
                    {"source": "REENTRY_EXACT_VERSION_UNAVAILABLE", "source_leg_id": source_leg_id},
                )
                continue

            result = gate.evaluate_owned_position(
                symbol, source["frame"], source["strategy_id"], state_mode=True,
                strategy_instance_id=instance_id, exact_strategy_version=source["strategy_version"],
            )
            state_side = "LONG" if str(result.get("side") or "").upper() == "BUY" else "SHORT" if str(result.get("side") or "").upper() == "SELL" else ""
            if int(result.get("allow") or 0) != 1 or not state_side:
                continue
            if state_side != target_side:
                self.runtime.ledger.record_reentry_cancelled(
                    source_leg_id,
                    "CURRENT_STATE_OPPOSITE_TO_REENTRY_TARGET",
                    {"target_side": target_side, "current_state_side": state_side},
                )
                continue

            result = dict(result)
            result["frame"] = str(source["frame"]).upper()
            result["strategy"] = str(source["strategy_id"]).upper()
            result["leverage"] = frame_row.get("leverage")
            plan = self._plan(symbol, result, instance, state_reentry=True)
            if not plan:
                continue
            units, leverage, price, risk, allocation = plan
            self._record_explicit_risk_evidence(instance_id, risk)
            base_signal = str(result.get("signal_id") or "")
            reentry_signal_id = f"{base_signal}|R4_REENTRY_SOURCE_{source_leg_id}"
            entry_policy = {
                "strategy_instance_id": instance_id,
                "frame": str(source["frame"]).upper(),
                "tier": str(instance.get("tier") or "").upper(),
                "capital_weight": float(allocation.get("final_weight") or 0.0),
                "tier_capital_weight": float(allocation.get("tier_weight") or 0.0),
                "allocation": allocation,
                "leverage": leverage,
                "entry_price": price,
                "entry_reference_price_ticks": self.exchange.price_to_ticks(symbol, price),
                "risk": risk,
                "exact_strategy_version": instance.get("exact_strategy_version"),
                "strategy_policy_bundle_version": instance.get("strategy_policy_bundle_version"),
                "policy_snapshot": instance.get("policy_snapshot") or {},
                "reentry_from_leg_id": source_leg_id,
                "reentry_policy_version": reentry_version,
                "reentry_source_exit_reason": exit_reason,
                "reentry_source_exit_ts": exit_ts,
                "reentry_gate_signal_id": base_signal,
            }
            capacity_limit = self._capacity(
                symbol,
                target_side,
                units,
            )

            entry_policy[
                "capacity_policy_mode"
            ] = "LIVE_RISK_APPROVED_INCREMENT"

            entry_policy[
                "capacity_limit_units"
            ] = int(capacity_limit)

            response = self.runtime.enter(
                instance_id,
                reentry_signal_id,
                symbol,
                target_side,
                units,
                entry_policy,
                capacity_limit,
                self._tier_concurrent_leg_limit(instance),
            )
            successor = str((response.get("intent") or {}).get("leg_id") or "")
            if successor:
                self.runtime.ledger.record_reentry_created(
                    source_leg_id,
                    successor,
                    reentry_signal_id,
                    {"status": response.get("status"), "policy_version": reentry_version, "target_side": target_side},
                )
                created.add((instance_id, symbol))
                _log(
                    "info",
                    f"REENTRY:{source_leg_id}:{successor}:{response.get('status')}",
                    f"[주문][R4][REENTRY] source_leg={source_leg_id} new_leg={successor} instance={instance_id} symbol={symbol} side={target_side} status={response.get('status')}",
                    sec=300,
                )
        return created

    # ========================================================
    # R4_ENTRY_PRIORITY_V1
    #
    # Lifecycle/reentry is already executed before this method
    # is used by cycle().
    #
    # New-entry order:
    #   NORMAL > SMALL > CANARY
    #
    # Same-tier:
    #   WF OOS
    #   > cost stress 2X
    #   > lower MDD
    #   > robustness
    #   > shorter DD duration
    #   > deterministic Strategy Instance ID
    #
    # This only changes evaluation order.
    # It never bypasses _plan(), allocation, risk, ceiling,
    # exchange filters, leverage, or Frozen Core.
    # ========================================================

    def _r4_entry_priority_key(self, instance):
        row=(
            instance
            if isinstance(instance,dict)
            else {}
        )

        def num(value,default=None):
            try:
                if isinstance(value,bool):
                    return 1.0 if value else 0.0
                x=float(value)
                if x == x:
                    return x
            except Exception:
                pass
            return default

        def find(obj,names,depth=0):
            if depth>4:
                return None

            wanted={
                str(x).strip().lower()
                for x in names
            }

            if isinstance(obj,dict):
                for key,value in obj.items():
                    if str(key).strip().lower() in wanted:
                        n=num(value)
                        if n is not None:
                            return n

                for value in obj.values():
                    if isinstance(
                        value,
                        (dict,list,tuple),
                    ):
                        found=find(
                            value,
                            wanted,
                            depth+1,
                        )

                        if found is not None:
                            return found

            elif isinstance(obj,(list,tuple)):
                for value in obj:
                    found=find(
                        value,
                        wanted,
                        depth+1,
                    )

                    if found is not None:
                        return found

            return None

        tier=str(
            row.get("tier")
            or row.get("deployment_tier")
            or ""
        ).upper()

        controller=strategy_runtime.r4_tier_controller()
        benefits=controller.get("tier_entry_benefits",{}) if isinstance(controller,dict) else {}
        if isinstance(benefits,dict) and benefits.get("enabled") is True:
            tier_rank=tier_priority_rank(tier,benefits.get("priority_order",()))
        else:
            tier_rank={"NORMAL":0,"SMALL":1,"CANARY":2}.get(tier,3)

        wf=find(
            row,
            (
                "walk_forward_oos",
                "walk_forward_avg_net_pct",
                "wf_oos_expectancy",
                "walk_forward_expectancy",
                "expected_return",
            ),
        )

        stress=find(
            row,
            (
                "cost_stress_2x",
                "cost_stress_2x_net_pct",
                "cost_stress_2x_avg_net_pct",
                "stress_2x_avg_net_pct",
            ),
        )

        mdd=find(
            row,
            (
                "mdd",
                "mdd_pct",
                "max_drawdown_pct",
                "maximum_drawdown_pct",
            ),
        )

        robustness=find(
            row,
            (
                "robustness",
                "robustness_score",
                "robust_score",
            ),
        )

        dd_duration=find(
            row,
            (
                "dd_duration",
                "drawdown_duration",
                "max_drawdown_duration",
                "drawdown_duration_hours",
            ),
        )

        instance_id=str(
            row.get("strategy_instance_id")
            or ""
        )

        # Higher-is-better metrics are negated for ascending sort.
        # Missing values sort behind measured values.
        return (
            tier_rank,

            1 if wf is None else 0,
            -(wf if wf is not None else 0.0),

            1 if stress is None else 0,
            -(stress if stress is not None else 0.0),

            1 if mdd is None else 0,
            (mdd if mdd is not None else 0.0),

            1 if robustness is None else 0,
            -(
                robustness
                if robustness is not None
                else 0.0
            ),

            1 if dd_duration is None else 0,
            (
                dd_duration
                if dd_duration is not None
                else 0.0
            ),

            instance_id,
        )

    def _tier_concurrent_leg_limit(self, instance):
        controller=strategy_runtime.r4_tier_controller()
        benefits=controller.get("tier_entry_benefits",{}) if isinstance(controller,dict) else {}
        if not isinstance(benefits,dict) or benefits.get("enabled") is not True:
            return None
        limits=benefits.get("concurrent_leg_limits")
        if not isinstance(limits,dict):
            raise RuntimeError("TIER_CONCURRENT_LEG_LIMITS_MISSING")
        row=instance if isinstance(instance,dict) else {}
        tier=str(row.get("tier") or row.get("deployment_tier") or "").upper()
        limit=limits.get(tier)
        if isinstance(limit,bool) or not isinstance(limit,int) or limit<=0:
            raise RuntimeError("INVALID_TIER_CONCURRENT_LEG_LIMIT:"+tier)
        return limit

    def cycle(self):
        self._reconcile_cycle_pass=False
        self._reconcile_cycle_started_at=int(time.time())
        self._critical_fail_instances_this_cycle = set()  # R4_SERVER_CRITICAL_EVIDENCE_CYCLE_V2
        self.runtime.heartbeat()

        # config.json Universe? ????.
        # ? cycle?? atomic snapshot? ?? ???.
        latest_cfg = _config()

        latest_symbols = (
            _symbols(latest_cfg)
            if isinstance(
                latest_cfg,
                dict,
            )
            else []
        )

        universe_ready = bool(
            latest_cfg
            and latest_symbols
        )

        if universe_ready:
            self.cfg = latest_cfg

            # HOT_RELOAD_CONFIG_TO_LEDGER_RECONCILE_V1
            # New approved peer Strategy Instances must exist in the
            # transactional ledger before reconcile/tier evidence runs.
            self._register_config_instances()

        else:
            self.runtime.disable_entry_admission(
                "ACTIVE_UNIVERSE_RELOAD_FAILED"
            )

            _log(
                "error",
                "R4_ACTIVE_UNIVERSE_RELOAD_FAILED",
                (
                    "[??][R4][ENTRY_BLOCK] "
                    "reason=ACTIVE_UNIVERSE_RELOAD_FAILED"
                ),
                sec=60,
            )

        try:
            reports = self.runtime.reconcile_all()
        except Exception as exc:
            self.runtime.disable_entry_admission("EXCHANGE_RECONCILE_SNAPSHOT_FAILED")
            _log(
                "error",
                "R4_RECONCILE_SNAPSHOT_FAILED",
                f"[주문][R4][ENTRY_BLOCK] reason=EXCHANGE_RECONCILE_SNAPSHOT_FAILED err={type(exc).__name__}",
                sec=60,
            )
            return 0
        # restart/WS 단절 사이에 남은 deterministic intent는 reconcile 후에만 복구한다.
        if self.runtime.ledger.pending_execution_count() > 0:
            try:
                self.runtime.recover_pending()
                reports = self.runtime.reconcile_all()
            except Exception as exc:
                self.runtime.disable_entry_admission("PENDING_EXECUTION_RECOVERY_FAILED")
                _log(
                    "error",
                    "R4_PENDING_RECOVERY_FAIL:" + type(exc).__name__,
                    f"[주문][R4][ENTRY_BLOCK] reason=PENDING_EXECUTION_RECOVERY_FAILED err={type(exc).__name__}",
                    sec=60,
                )
                return 0

        # 기존 exposure 관리는 신규 진입 guard보다 먼저 수행한다.
        reconcile_ok = all(x.get("ok") for x in reports)

        active_rows = strategy_runtime.r4_strategy_instances()
        if not reconcile_ok:
            self.runtime.disable_entry_admission("BASKET_RECONCILE_MISMATCH")
            bad = {(str(x.get("symbol") or "").upper(), str(x.get("side") or "").upper()) for x in reports if not x.get("ok")}
            legs = self.runtime.ledger.active_legs()
            for instance_id, row in active_rows.items():
                if row.get("approved_for_server_release") is not True:
                    continue
                owned_bad = any((str(x.get("symbol") or "").upper(), str(x.get("position_side") or "").upper()) in bad and x.get("instance_id") == instance_id for x in legs)
                if owned_bad:
                    self.runtime.record_scale_evidence(
                        instance_id,
                        "STRATEGY_LEG_LEDGER_RECONCILE_PASS",
                        False,
                        {"source": "BASKET_RECONCILE", "baskets": sorted([list(x) for x in bad])},
                    )
                    self._record_server_owned_boolean_evidence(
                        instance_id,
                        "NO_CRITICAL_HARD_FAIL",
                        False,
                        {"source": "BASKET_RECONCILE_MISMATCH"},
                    )
            _log("error", "R4_RECONCILE_MISMATCH", "[주문][R4][ENTRY_BLOCK] reason=BASKET_RECONCILE_MISMATCH", sec=60)
            # 신규 진입은 차단한 채, 정합성 PASS인 기존 바스켓만 익절 관리한다.
            # 불일치 바스켓은 변경·청산하지 않으며 다음 reconcile을 기다린다.
            self._manage_active_legs(blocked_pairs=bad)
            self._tier_cycle()
            return 0

        for instance_id, row in active_rows.items():
            if row.get("approved_for_server_release") is True:
                self.runtime.record_scale_evidence(
                    instance_id,
                    "STRATEGY_LEG_LEDGER_RECONCILE_PASS",
                    True,
                    {"source": "BASKET_RECONCILE"},
                )

        if not self.runtime.entry_admission_enabled:
            try:
                self.runtime.enable_entry_admission_after_reconcile(reports)
                _log(
                    "info",
                    "R4_ENTRY_ADMISSION_ENABLED",
                    "[주문][R4][CUTOVER] 신규 진입 활성화: 전체 basket reconcile PASS",
                    sec=300,
                )
            except Exception as exc:
                _log(
                    "error",
                    "R4_ENTRY_ADMISSION_ENABLE_FAIL",
                    f"[주문][R4][ENTRY_BLOCK] reason=ENTRY_ADMISSION_ENABLE_FAIL err={type(exc).__name__}",
                    sec=60,
                )
                return 0

        self._reconcile_cycle_pass=True
        managed_pairs = self._manage_active_legs()
        # 청산 결과를 반영한 등급·연패 차단을 재진입과 신규 진입보다 먼저 적용한다.
        self._tier_cycle()

        if not universe_ready:
            return 0

        if not self.runtime.entry_admission_enabled:
            return 0

        guard = frozen_kernel.v3_account_mode_guard()
        if not guard.get("allow"):
            _log("info", "ACCOUNT_MODE_GUARD", "[주문][R4][ENTRY_BLOCK] reason=ACCOUNT_MODE_GUARD", sec=60)
            self._tier_cycle()
            return 0

        try:
            reentry_pairs = self._manage_reentries(managed_pairs)
        except Exception as exc:
            self.runtime.disable_entry_admission("REENTRY_LOOP_FAILED")
            _log(
                "error",
                "R4_REENTRY_LOOP_FAILED:" + type(exc).__name__,
                f"[주문][R4][ENTRY_BLOCK] reason=REENTRY_LOOP_FAILED err={type(exc).__name__}",
                sec=60,
            )
            self._tier_cycle()
            return 0
        blocked_pairs = set(managed_pairs) | set(reentry_pairs)

        entered = len(reentry_pairs)
        result_cache = {}
        # R4_ENTRY_PRIORITY_V1
        # Reentries were handled above. Only NEW entry evaluation
        # order is sorted here; every original gate still executes.
        entry_instances = list(
            strategy_runtime.r4_enabled_instances()
        )

        entry_instances.sort(
            key=self._r4_entry_priority_key
        )

        for instance in entry_instances:
            instance_id = str(instance.get("strategy_instance_id") or "")
            if not instance_id or instance.get("entry_blocked") is True:
                continue
            frame = str(instance.get("timeframe") or "").upper()
            for symbol in _symbols(self.cfg):
                if (instance_id, symbol) in blocked_pairs:
                    continue
                cache_key = (symbol, frame, instance_id, str(instance.get("exact_strategy_version") or ""))
                result = result_cache.get(cache_key)
                if result is None:
                    result = gate.evaluate_frame(
                        symbol, frame, require_enabled=True, strategy_instance_id=instance_id,
                    )
                    result_cache[cache_key] = result
                if int(result.get("allow") or 0) != 1:
                    continue
                if not self._matches_result(instance, result):
                    continue
                plan = self._plan(symbol, result, instance)
                if not plan:
                    continue
                units, leverage, price, risk, allocation = plan
                self._record_explicit_risk_evidence(instance_id, risk)
                side = "LONG" if result["side"] == "BUY" else "SHORT"
                policy = {
                    "strategy_instance_id": instance_id,
                    "frame": frame,
                    "tier": str(instance.get("tier") or "").upper(),
                    "capital_weight": float(allocation.get("final_weight") or 0.0),
                    "tier_capital_weight": float(allocation.get("tier_weight") or 0.0),
                    "allocation": allocation,
                    "leverage": leverage,
                    "entry_price": price,
                    "entry_reference_price_ticks": self.exchange.price_to_ticks(symbol, price),
                    "risk": risk,
                    "exact_strategy_version": instance.get("exact_strategy_version"),
                    "strategy_policy_bundle_version": instance.get("strategy_policy_bundle_version"),
                    "policy_snapshot": instance.get("policy_snapshot") or {},
                }
                capacity_limit = self._capacity(
                    symbol,
                    side,
                    units,
                )

                policy[
                    "capacity_policy_mode"
                ] = "LIVE_RISK_APPROVED_INCREMENT"

                policy[
                    "capacity_limit_units"
                ] = int(capacity_limit)

                response = self.runtime.enter(
                    instance_id,
                    result["signal_id"],
                    symbol,
                    side,
                    units,
                    policy,
                    capacity_limit,
                    self._tier_concurrent_leg_limit(instance),
                )
                _log(
                    "info",
                    f"ENTRY:{instance_id}:{symbol}:{result['signal_id']}:{response.get('status')}",
                    f"[주문][R4][ENTRY_RESULT] instance={instance_id} symbol={symbol} frame={frame} status={response.get('status')}",
                    sec=300,
                )
                entered += int(response.get("status") == "PROCESSED")

        self._record_successful_cycle_no_critical(
            active_rows
        )
        return entered

# ================= 메인 =================
def main():
    stop_requested = [False]

    def _request_shutdown(signum, frame):
        stop_requested[0] = True

    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    service = R4RuntimeService()
    try:
        if stop_requested[0]:
            return
        service.runtime.recover_pending()
        service.user_stream.start()
        # PROCESS_RESTART 헌법: 매 기동마다 신규 진입은 닫힌 상태에서 즉시 전체 reconcile부터 수행한다.
        try:
            reports = service.runtime.reconcile_all()
            service.runtime.enable_entry_admission_after_reconcile(reports)
            _log(
                "info",
                "R4_STARTUP_RECONCILE_PASS",
                "[주문][R4][CUTOVER] startup reconcile PASS, 신규 진입 활성화",
                sec=300,
            )
        except Exception as exc:
            service.runtime.disable_entry_admission("STARTUP_RECONCILE_NOT_PASS")
            _log(
                "error",
                "R4_STARTUP_RECONCILE_BLOCK",
                f"[주문][R4][ENTRY_BLOCK] reason=STARTUP_RECONCILE_NOT_PASS err={type(exc).__name__}",
                sec=60,
            )
        while not stop_requested[0]:
            service.cycle()
            if stop_requested[0]:
                break
            sleep_sec = max(5, int(os.getenv("R4_LOOP_SEC", "15")))
            deadline = time.time() + sleep_sec
            while not stop_requested[0] and time.time() < deadline:
                time.sleep(min(0.25, max(0.0, deadline - time.time())))
    finally:
        service.close()


if __name__ == "__main__":
    main()
