# v3_portfolio_allocation_gate.py
# B-Bot V3
# GREEN-7A-2 Portfolio Allocation Gate

PORTFOLIO_ALLOCATION_GATE_V1 = True
ADAPTIVE_CAPITAL_BOOST_V1 = True

REQUIRED_CONSTRAINTS = (
    "entry_risk_safe_size_pass",
    "account_risk_budget_pass",
    "portfolio_total_exposure_pass",
    "available_margin_pass",
    "margin_safety_reserve_pass",
    "symbol_concentration_pass",
    "strategy_concentration_pass",
    "correlation_concentration_pass",
    "cost_liquidity_scale_pass",
)

GROWTH_BOUNDARY_USDT = 70_000.0

ADAPTIVE_CAPITAL_BOOST_LEVELS = {
    "CANARY": {"default": 1.80, "strong": 1.95, "hard_max": 2.20},
    "SMALL": {"default": 2.20, "strong": 2.60, "hard_max": 3.00},
    "NORMAL": {"default": 2.80, "strong": 3.20, "hard_max": 3.50},
}


def _finite(value):
    try:
        x = float(value)
        if x == x and abs(x) != float("inf"):
            return x
    except Exception:
        pass
    return None


def adaptive_capital_boost_decision(evidence, target_tier, ranked_row=None):
    """Sub-100M operating proxy boost. Never changes exchange leverage.

    The existing GROWTH boundary (<70k USDT) is the operational proxy; no FX
    dependency is introduced. Missing account evidence fails back to 1.0x.
    """
    evidence = evidence if isinstance(evidence, dict) else {}
    ranked_row = ranked_row if isinstance(ranked_row, dict) else {}
    tier = str(target_tier or "").upper()
    levels = ADAPTIVE_CAPITAL_BOOST_LEVELS.get(tier)
    base = {
        "adaptive_capital_boost_policy": "SUB_100M_AGGRESSIVE_GROWTH_ADAPTIVE_CAPITAL_BOOST_V1",
        "capital_boost_multiplier": 1.0,
        "capital_boost_level": "OFF",
        "capital_boost_hard_max": float((levels or {}).get("hard_max", 1.0)),
        "capital_boost_exchange_leverage_changed": False,
    }
    if not levels:
        base["capital_boost_reason"] = "TIER_NOT_ELIGIBLE"
        return base

    mode = str(evidence.get("account_mode") or "").upper()
    equity = _finite(evidence.get("account_avg_equity_usdt_24h"))
    if mode != "GROWTH":
        base["capital_boost_reason"] = "ACCOUNT_MODE_NOT_GROWTH"
        return base
    if equity is None or equity >= GROWTH_BOUNDARY_USDT:
        base["capital_boost_reason"] = "EQUITY_NOT_SUB_100M_PROXY_OR_UNKNOWN"
        return base
    if evidence.get("available_margin_pass") is not True:
        base["capital_boost_reason"] = "AVAILABLE_MARGIN_NOT_PASS"
        return base

    try:
        hard_fail = int(ranked_row.get("hard_fail_count") or 0)
    except Exception:
        hard_fail = 0
    if hard_fail > 0:
        base["capital_boost_reason"] = "HARD_FAIL_EVIDENCE"
        return base

    multiplier = float(levels["default"])
    level = "DEFAULT"
    profile = str(ranked_row.get("live_profile_class") or "").upper()
    percentile = _finite(ranked_row.get("rank_percentile"))

    if profile == "GROWTH":
        multiplier = float(levels["strong"])
        level = "STRONG"
        if percentile is not None and percentile >= 0.75:
            multiplier = float(levels["hard_max"])
            level = "HARD_MAX"

    multiplier = min(float(levels["hard_max"]), max(1.0, multiplier))
    base.update({
        "capital_boost_multiplier": multiplier,
        "capital_boost_level": level,
        "capital_boost_reason": "SUB_100M_GROWTH_ELIGIBLE",
        "capital_boost_account_mode": mode,
        "capital_boost_avg_equity_usdt_24h": equity,
        "capital_boost_live_profile_class": profile,
        "capital_boost_rank_percentile": percentile,
    })
    return base


def evaluate_portfolio_allocation(
    ranking_result,
    strategy_version,
    target_tier,
    evidence,
):
    """Mode-aware risk gate with ranking as reference-only.

    Adaptive boost is a bounded capital-weight multiplier applied only after
    the existing allocation gate has passed. It never changes exchange
    leverage and never overrides rollback/demotion or a failed allocation.
    """
    evidence = evidence if isinstance(evidence, dict) else {}
    mode = str(evidence.get("account_mode") or "").upper()

    # FINAL_CONSTITUTION_RUNTIME_ALIGNMENT_STAGE12_V1
    # Account mode may change ranking/boost aggressiveness, never whether common
    # portfolio safety evidence is required. Missing evidence fails closed.
    required = REQUIRED_CONSTRAINTS

    failed = [key for key in required if evidence.get(key) is not True]
    if failed:
        out = {
            "ok": False,
            "decision": "BLOCK",
            "reason": "PORTFOLIO_CONSTRAINT_MISSING_OR_FAIL",
            "failed_constraints": failed,
            "weight_multiplier": 0.0,
            "ranking_role": "REFERENCE_ONLY_NON_BLOCKING",
        }
        out.update(adaptive_capital_boost_decision({}, target_tier, None))
        return out

    rows = []
    wanted = None
    ranking_ok = isinstance(ranking_result, dict) and ranking_result.get("ok")
    if ranking_ok:
        rows = ranking_result.get("ranked") or []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("strategy_version") or "") == str(strategy_version or ""):
                wanted = row
                break

    if not ranking_ok:
        reason = "RANKING_REFERENCE_ONLY_FALLBACK"
        multiplier = 1.0
    elif wanted is None:
        reason = "RANKING_VERSION_REFERENCE_ONLY_FALLBACK"
        multiplier = 1.0
    else:
        total_score = 0.0
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                score = float(row.get("score") or 0.0)
            except Exception:
                score = 0.0
            total_score += max(0.0, score)
        if total_score <= 0.0:
            reason = "RANKING_SCORE_REFERENCE_ONLY_FALLBACK"
            multiplier = 1.0
        else:
            try:
                own = max(0.0, float(wanted.get("score") or 0.0))
            except Exception:
                own = 0.0
            multiplier = max(0.0, min(1.0, own / total_score))
            reason = "BOUNDED_SCORE_WEIGHTED_WITH_PORTFOLIO_CAPS"

    boost = adaptive_capital_boost_decision(evidence, target_tier, wanted)
    out = {
        "ok": True,
        "decision": "ALLOW",
        "reason": reason,
        "weight_multiplier": float(multiplier),
        "target_tier": str(target_tier or "").upper(),
        "ranking_role": "REFERENCE_ONLY_NON_BLOCKING",
        "profile_overrides_safety": False,
    }
    if wanted is not None:
        out.update({
            "rank": wanted.get("rank"),
            "rank_percentile": wanted.get("rank_percentile"),
            "score": float(wanted.get("score") or 0.0),
            "live_profile_class": str(wanted.get("live_profile_class") or ""),
            "profile_alignment_multiplier": float(wanted.get("profile_alignment_multiplier") or 1.0),
            "profile_role": "SELECTION_PRIORITY_WITHIN_EXISTING_SAFETY_GATES",
        })
    out.update(boost)
    return out
