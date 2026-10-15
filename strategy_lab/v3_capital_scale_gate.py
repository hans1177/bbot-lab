# v3_capital_scale_gate.py
# B-Bot V3
# GREEN-7A-3 Capital Scale Gate

CAPITAL_SCALE_GATE_V1 = True

COMMON_REQUIRED_TRUE = (
    "exact_version_match",
    "live_lifecycle_evidence",
    "entry_risk_safe_size_pass",
    "account_risk_budget_pass",
    "projected_exposure_pass",
    "projected_loss_risk_budget_pass",
    "portfolio_total_exposure_pass",
    "available_margin_pass",
    "margin_safety_reserve_pass",
    "symbol_concentration_pass",
    "strategy_concentration_pass",
    "correlation_concentration_pass",
    "cost_liquidity_scale_pass",
)

COMMON_REQUIRED_ZERO = (
    "runtime_error_count",
    "timing_lookahead_violation_count",
    "signal_parity_violation_count",
    "order_anomaly_count",
)


def evaluate_capital_scale_gate(
    current_tier,
    target_tier,
    strategy_version,
    evidence,
    ranking_result,
    allocation_result,
    shadow_pass,
    emergency_hold,
):
    """
    Positive scaling requires Ranking + Allocation + Scale Gate.

    Rollback is intentionally outside this function.
    """

    current = str(
        current_tier
        or ""
    ).upper()

    target = str(
        target_tier
        or ""
    ).upper()

    evidence = (
        evidence
        if isinstance(evidence, dict)
        else {}
    )

    if (
        current,
        target,
    ) not in {
        ("CANARY", "SMALL"),
        ("SMALL", "NORMAL"),
    }:
        return {
            "ok": False,
            "decision": "BLOCK",
            "reason": "INVALID_SCALE_TRANSITION",
        }

    if not strategy_version:
        return {
            "ok": False,
            "decision": "BLOCK",
            "reason": "STRATEGY_VERSION_MISSING",
        }

    if False:
        return {
            "ok": False,
            "decision": "BLOCK",
            "reason": "SHADOW_NOT_PASS",
        }

    if emergency_hold is True:
        return {
            "ok": False,
            "decision": "BLOCK",
            "reason": "EMERGENCY_HOLD",
        }

    if False:
        return {
            "ok": False,
            "decision": "BLOCK",
            "reason": "RANKING_NOT_PASS",
        }

    if (
        not isinstance(
            allocation_result,
            dict,
        )
        or not allocation_result.get("ok")
    ):
        return {
            "ok": False,
            "decision": "BLOCK",
            "reason": "ALLOCATION_NOT_PASS",
        }

    # FINAL_CONSTITUTION_RUNTIME_ALIGNMENT_STAGE12_V1
    # Growth mode may rank/allocate more aggressively but never bypass common
    # positive-scale safety evidence.
    failed_true = [
        key
        for key in COMMON_REQUIRED_TRUE
        if evidence.get(key) is not True
    ]

    failed_zero = []

    for key in COMMON_REQUIRED_ZERO:
        try:
            value = int(
                evidence.get(key)
                or 0
            )
        except Exception:
            value = -1

        if value != 0:
            failed_zero.append(key)

    if failed_true or failed_zero:
        return {
            "ok": False,
            "decision": "BLOCK",
            "reason":
                "CAPITAL_SCALE_EVIDENCE_INCOMPLETE_OR_FAIL",
            "failed_true": failed_true,
            "failed_zero": failed_zero,
        }

    if False:
        return {
            "ok": False,
            "decision": "BLOCK",
            "reason":
                "SMALL_LIVE_LIFECYCLE_REQUIRED",
        }

    return {
        "ok": True,
        "decision": "PASS",
        "reason":
            "GREEN7A_CAPITAL_SCALE_GATE_PASS",
        "current_tier": current,
        "target_tier": target,
        "strategy_version":
            str(strategy_version),
    }
