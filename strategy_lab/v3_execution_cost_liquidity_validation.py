# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(os.getenv("BBOT_LOCAL_ROOT", r"C:\my\bottest"))

CONSTITUTION = ROOT / "ROADMAP_CONSTITUTION_V3.json"
MEASUREMENT = ROOT / "strategy_lab" / "results" / "v3" / "execution_cost_liquidity_measurement.json"

# Independent evidence producers will populate these.
FEE_EVIDENCE = ROOT / "strategy_lab" / "results" / "v3" / "execution_fee_contract_evidence.json"
MICRO_EVIDENCE = ROOT / "strategy_lab" / "results" / "v3" / "execution_microstructure_validation.json"

OUT = ROOT / "strategy_lab" / "results" / "v3" / "execution_cost_liquidity_validation.json"

SCHEMA = "B_BOT_V3_EXECUTION_COST_LIQUIDITY_VALIDATION_V1"
EXPECTED_MEASUREMENT_SCHEMA = "B_BOT_V3_EXECUTION_COST_LIQUIDITY_MEASUREMENT_V1"
TELEMETRY_ROLE = "REFERENCE_ONLY_NON_BLOCKING_FOR_ALL_PROMOTIONS"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def atomic(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def nonnegative(value):
    try:
        x = float(value)
    except Exception:
        return None
    return x if x >= 0 else None


def run_once() -> dict[str, Any]:
    reasons = []

    constitution = load(CONSTITUTION)
    contract = constitution.get("validated_execution_cost_liquidity_policy_v1")

    if not isinstance(contract, dict):
        raise RuntimeError("COST_POLICY_CONSTITUTION_CONTRACT_MISSING")

    validation_contract = contract.get("validation_authority") or {}
    numeric_rules = contract.get("numeric_policy_rules") or {}
    fee_contract = contract.get("fee_validation_contract") or {}
    slip_contract = contract.get("slippage_liquidity_validation_contract") or {}

    if validation_contract.get("same_cycle_measurement_to_validation_forbidden") is not True:
        raise RuntimeError("SAME_CYCLE_VALIDATION_CONTRACT_INVALID")

    if numeric_rules.get("hardcoded_unvalidated_thresholds_forbidden") is not True:
        raise RuntimeError("UNVALIDATED_THRESHOLD_CONTRACT_INVALID")

    measurement = load(MEASUREMENT)

    if measurement.get("schema") != EXPECTED_MEASUREMENT_SCHEMA:
        reasons.append("MEASUREMENT_SCHEMA_INVALID")

    if measurement.get("actual_execution_telemetry_role") != TELEMETRY_ROLE:
        reasons.append("TELEMETRY_ROLE_INVALID")

    measurement_sha = sha256(MEASUREMENT)

    # --------------------------------------------------------
    # Fee threshold authority:
    # only authenticated account commission contract evidence.
    # Never simulator fee assumptions or actual-fill telemetry.
    # --------------------------------------------------------
    max_fee_bps = None
    fee_sha = None
    fee_ok = False

    if not FEE_EVIDENCE.is_file():
        reasons.append("AUTHENTICATED_FEE_CONTRACT_EVIDENCE_MISSING")
    else:
        fee = load(FEE_EVIDENCE)
        fee_sha = sha256(FEE_EVIDENCE)

        source_ok = (
            fee.get("authoritative_source")
            == fee_contract.get("authoritative_source")
        )

        scope_ok = (
            fee.get("symbol_scope")
            == fee_contract.get("symbol_scope")
        )

        rates = fee.get("taker_commission_bps_by_symbol")

        values = []
        if isinstance(rates, dict) and rates:
            for value in rates.values():
                x = nonnegative(value)
                if x is None:
                    values = []
                    break
                values.append(x)

        fee_ok = (
            fee.get("validated") is True
            and source_ok
            and scope_ok
            and bool(values)
        )

        if fee_ok:
            max_fee_bps = max(values)
        else:
            reasons.append("AUTHENTICATED_FEE_CONTRACT_EVIDENCE_INVALID")

    # --------------------------------------------------------
    # Slippage threshold authority:
    # only separately independently validated PIT
    # microstructure evidence.
    # Actual execution measurement cannot derive threshold.
    # --------------------------------------------------------
    max_slippage_bps = None
    micro_sha = None
    micro_ok = False

    if not MICRO_EVIDENCE.is_file():
        reasons.append("INDEPENDENT_PIT_MICROSTRUCTURE_VALIDATION_MISSING")
    else:
        micro = load(MICRO_EVIDENCE)
        micro_sha = sha256(MICRO_EVIDENCE)

        max_slippage_bps = nonnegative(
            micro.get("max_abs_slippage_bps")
        )

        micro_ok = (
            micro.get("validated") is True
            and micro.get("independent_validation") is True
            and micro.get("policy_candidate_allowed") is True
            and max_slippage_bps is not None
            and slip_contract.get(
                "point_in_time_microstructure_evidence_required_for_numeric_validation"
            ) is True
        )

        if not micro_ok:
            max_slippage_bps = None
            reasons.append("INDEPENDENT_PIT_MICROSTRUCTURE_VALIDATION_INVALID")

    validated = fee_ok and micro_ok and not reasons

    result = {
        "schema": SCHEMA,
        "generated_at": now(),
        "status": "VALIDATED" if validated else "INSUFFICIENT_EVIDENCE",
        "validated": bool(validated),
        "independent_validation": bool(validated),
        "policy_candidate_allowed": bool(validated),
        "live_effect": False,
        "actual_execution_telemetry_role": TELEMETRY_ROLE,
        "threshold_derivation_from_actual_execution_telemetry": False,
        "same_cycle_measurement_to_validation_forbidden": True,
        "measurement": {
            "path": str(MEASUREMENT.relative_to(ROOT)),
            "sha256": measurement_sha,
            "status": measurement.get("status"),
            "usable_observation_count": measurement.get("usable_observation_count"),
        },
        "fee_contract_evidence": {
            "path": str(FEE_EVIDENCE.relative_to(ROOT)),
            "sha256": fee_sha,
            "pass": fee_ok,
        },
        "microstructure_validation_evidence": {
            "path": str(MICRO_EVIDENCE.relative_to(ROOT)),
            "sha256": micro_sha,
            "pass": micro_ok,
        },
        "numeric_thresholds": (
            {
                "max_fee_bps": max_fee_bps,
                "max_abs_slippage_bps": max_slippage_bps,
            }
            if validated
            else None
        ),
        "reasons": reasons,
        "next_required_stage": (
            "POLICY_CANDIDATE_REGISTRATION"
            if validated
            else "COLLECT_AND_INDEPENDENTLY_VALIDATE_REQUIRED_EVIDENCE"
        ),
    }

    atomic(OUT, result)
    return result


if __name__ == "__main__":
    r = run_once()
    print("STATUS=" + str(r.get("status")))
    print("VALIDATED=" + str(r.get("validated")))
    print("INDEPENDENT_VALIDATION=" + str(r.get("independent_validation")))
    print("POLICY_CANDIDATE_ALLOWED=" + str(r.get("policy_candidate_allowed")))
    print("NUMERIC_THRESHOLDS=" + repr(r.get("numeric_thresholds")))
    print("REASONS=" + repr(r.get("reasons")))
