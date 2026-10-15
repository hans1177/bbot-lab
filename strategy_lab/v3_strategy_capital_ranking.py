# v3_strategy_capital_ranking.py
# B-Bot V3
# GREEN-7A-1 Strategy Capital Ranking

STRATEGY_CAPITAL_RANKING_V1 = True
CONTINUOUS_ACCOUNT_SIZE_RANKING_BIAS_V1 = True

MODE_WEIGHTS = {
    "GROWTH": {
        "growth_efficiency": 0.35,
        "risk_efficiency": 0.20,
        "stability": 0.15,
        "execution_quality": 0.10,
        "cost_efficiency": 0.10,
        "diversification_value": 0.10,
    },
    "BALANCED": {
        "growth_efficiency": 0.25,
        "risk_efficiency": 0.25,
        "stability": 0.20,
        "execution_quality": 0.10,
        "cost_efficiency": 0.10,
        "diversification_value": 0.10,
    },
    "DEFENSIVE": {
        "growth_efficiency": 0.15,
        "risk_efficiency": 0.30,
        "stability": 0.25,
        "execution_quality": 0.10,
        "cost_efficiency": 0.10,
        "diversification_value": 0.10,
    },
}

# User-approved maxima. Maxima are not quotas.
TIER_MAXIMA = {
    'CANARY': 8,
    'SMALL': 5,
    'NORMAL': 3,
}

GROWTH_BOUNDARY_USDT = 70_000.0
DEFENSIVE_BOUNDARY_USDT = 350_000.0
HIGH_EQUITY_REFERENCE_USDT = None

AXES = tuple(MODE_WEIGHTS["GROWTH"].keys())


def _clamp01(value):
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


def _finite(value):
    try:
        x = float(value)
        if x == x and abs(x) != float("inf"):
            return x
    except Exception:
        pass
    return None


PROFILE_ALIGNMENT_MULTIPLIER = {
    "GROWTH": {"GROWTH": 1.08, "BALANCED": 1.00, "DEFENSIVE": 0.96},
    "BALANCED": {"GROWTH": 0.99, "BALANCED": 1.05, "DEFENSIVE": 0.99},
    "DEFENSIVE": {"GROWTH": 0.96, "BALANCED": 1.00, "DEFENSIVE": 1.08},
}


def _official_live_profile(evidence):
    evidence = evidence if isinstance(evidence, dict) else {}
    row = evidence.get("strategy_instance_live_profile")
    if not isinstance(row, dict):
        row = evidence.get("live_strategy_profile")
    if not isinstance(row, dict):
        return None
    if str(row.get("classification_status") or "").upper() != "CLASSIFIED":
        return None
    klass = str(row.get("official_class") or "").upper()
    if klass not in {"GROWTH", "BALANCED", "DEFENSIVE"}:
        return None
    return row


def _profile_alignment(evidence, account_mode, target_tier=""):
    tier = str(target_tier or evidence.get("current_tier") or "").upper() if isinstance(evidence, dict) else str(target_tier or "").upper()
    row = _official_live_profile(evidence)
    if row is None or tier == "CANARY":
        return 1.0, "UNCLASSIFIED_OR_CANARY_NEUTRAL", ""
    klass = str(row.get("official_class") or "").upper()
    mode = str(account_mode or "").upper()
    mult = float((PROFILE_ALIGNMENT_MULTIPLIER.get(mode) or {}).get(klass, 1.0))
    return mult, "LIVE_PROFILE_RELATIVE_PRIORITY", klass


def _normalize_weights(weights):
    row = {name: max(0.0, float(weights.get(name, 0.0))) for name in AXES}
    total = sum(row.values())
    if total <= 0.0:
        return dict(MODE_WEIGHTS["DEFENSIVE"])
    return {name: row[name] / total for name in AXES}


def _blend(a, b, t):
    t = _clamp01(t)
    return _normalize_weights({
        name: float(a[name]) * (1.0 - t) + float(b[name]) * t
        for name in AXES
    })


def continuous_mode_weights(account_mode, avg_equity_usdt=None):
    """Continuous ranking bias without inventing hard risk-budget thresholds.

    GROWTH keeps its existing flexible weights. BALANCED smoothly approaches
    the existing DEFENSIVE weights between the already-approved 100M/500M
    boundaries. Above 500M, only the existing DEFENSIVE growth weight is
    progressively transferred to its existing risk/stability axes. At 1B the
    transfer is halfway complete; 1B is a reference, not a fourth mode.
    """
    mode = str(account_mode or "").upper()
    if mode not in MODE_WEIGHTS:
        return None

    equity = _finite(avg_equity_usdt)
    if equity is None or equity < 0.0:
        return dict(MODE_WEIGHTS[mode])

    if mode == "GROWTH" or equity < GROWTH_BOUNDARY_USDT:
        return dict(MODE_WEIGHTS["GROWTH"])

    if mode == "BALANCED":
        t = (
            (equity - GROWTH_BOUNDARY_USDT)
            / (DEFENSIVE_BOUNDARY_USDT - GROWTH_BOUNDARY_USDT)
        )
        return _blend(
            MODE_WEIGHTS["BALANCED"],
            MODE_WEIGHTS["DEFENSIVE"],
            t,
        )

    # DEFENSIVE: progressively shift only the already-existing growth weight.
    base = dict(MODE_WEIGHTS["DEFENSIVE"])
    if equity <= DEFENSIVE_BOUNDARY_USDT:
        return base

    extra = 1.0 - (
        DEFENSIVE_BOUNDARY_USDT
        / max(equity, DEFENSIVE_BOUNDARY_USDT)
    )
    shift = base["growth_efficiency"] * _clamp01(extra)
    base["growth_efficiency"] -= shift

    # Reallocate the shift in the existing DEFENSIVE risk:stability proportion.
    rs_total = base["risk_efficiency"] + base["stability"]
    if rs_total > 0.0:
        base["risk_efficiency"] += shift * base["risk_efficiency"] / rs_total
        base["stability"] += shift * base["stability"] / rs_total

    return _normalize_weights(base)


def _derived_axes(evidence):
    evidence = evidence if isinstance(evidence, dict) else {}
    avg_roi = _finite(evidence.get("avg_roi")) or 0.0
    lo = _finite(evidence.get("roi_95ci_lower")) or 0.0
    hi = _finite(evidence.get("roi_95ci_upper"))
    if hi is None:
        hi = lo
    width = max(0.0, hi - lo)
    hard_fail = int(evidence.get("hard_fail_count") or 0)

    return {
        "growth_efficiency": _clamp01(0.5 + avg_roi / 10.0),
        "risk_efficiency": 0.0 if hard_fail else _clamp01(0.5 + lo / 10.0),
        "stability": 0.0 if hard_fail else _clamp01(1.0 - width / 20.0),
        "execution_quality": _clamp01(evidence.get("execution_quality_score", 0.0)),
        "cost_efficiency": _clamp01(evidence.get("cost_efficiency_score", 0.0)),
        "diversification_value": _clamp01(evidence.get("diversification_value_score", 0.0)),
    }


def rank_strategies(candidates, account_mode, avg_equity_usdt=None):
    """Rank approved exact Strategy Versions only.

    Ranking never overrides safety and never promotes a tier. Account size may
    change relative ranking bias, but not liquidation safety or hard caps.
    """
    mode = str(account_mode or "").upper()
    weights = continuous_mode_weights(mode, avg_equity_usdt)

    if weights is None:
        return {
            "ok": False,
            "decision": "BLOCK",
            "reason": "ACCOUNT_MODE_UNKNOWN",
            "account_mode": mode,
            "ranked": [],
        }

    ranked = []
    for candidate in candidates if isinstance(candidates, list) else []:
        if not isinstance(candidate, dict):
            continue

        strategy = str(candidate.get("strategy") or "").upper()
        version = str(candidate.get("strategy_version") or "")
        status = str(candidate.get("status") or "").upper()
        timeframe = str(candidate.get("timeframe") or "").upper()

        if (
            not strategy
            or not version
            or status not in {
                "PROVISIONAL", "PROVISIONAL_MAIN", "VALIDATED", "VALIDATED_MAIN",
                "CANARY", "SMALL", "NORMAL",
            }
        ):
            continue

        evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {}
        axes = _derived_axes(evidence)
        base_score = sum(axes[name] * weights[name] for name in AXES)
        target_tier = str(candidate.get("target_tier") or status).upper()
        profile_multiplier, profile_role, profile_class = _profile_alignment(
            evidence, mode, target_tier
        )
        score = float(base_score) * float(profile_multiplier)

        ranked.append({
            "strategy": strategy,
            "strategy_version": version,
            "timeframe": timeframe,
            "score": float(score),
            "base_score": float(base_score),
            "axes": axes,
            "hard_fail_count": int(evidence.get("hard_fail_count") or 0),
            "live_profile_class": profile_class,
            "profile_alignment_multiplier": float(profile_multiplier),
            "profile_role": profile_role,
        })

    ranked.sort(key=lambda row: (-float(row.get("score") or 0.0), str(row.get("strategy_version") or "")))
    peer_count = len(ranked)
    for index, row in enumerate(ranked, 1):
        row["rank"] = index
        row["rank_percentile"] = (
            1.0
            if peer_count <= 1
            else 1.0 - float(index - 1) / float(peer_count - 1)
        )

    return {
        "ok": bool(ranked),
        "decision": "PASS" if ranked else "BLOCK",
        "reason": "RANKED" if ranked else "NO_APPROVED_STRATEGY",
        "account_mode": mode,
        "avg_equity_usdt": _finite(avg_equity_usdt),
        "continuous_mode_weights": weights,
        "high_equity_reference_usdt": HIGH_EQUITY_REFERENCE_USDT,
        "ranked": ranked,
    }


def _survival_admissible(candidate, target_tier):
    """Absolute safety/admissibility floor only; performance remains relative."""
    if not isinstance(candidate, dict):
        return False, "INVALID_CANDIDATE"
    evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {}

    if int(evidence.get("hard_fail_count") or 0) > 0:
        return False, "HARD_FAIL"

    # Existing explicit safety failures are absolute. Missing values are not
    # silently converted into a performance advantage here; downstream gates
    # remain authoritative/fail-closed.
    for key in (
        "entry_risk_safe_size_pass",
        "account_risk_budget_pass",
        "portfolio_total_exposure_pass",
        "available_margin_pass",
        "margin_safety_reserve_pass",
        "symbol_concentration_pass",
        "strategy_concentration_pass",
        "correlation_concentration_pass",
        "cost_liquidity_scale_pass",
    ):
        if key in evidence and evidence.get(key) is False:
            return False, key.upper() + "_FAIL"

    tier = str(target_tier or "").upper()
    if tier in {"SMALL", "NORMAL"}:
        if evidence.get("live_lifecycle_evidence") is False:
            return False, "LIVE_LIFECYCLE_FAIL"
    if tier == "NORMAL" and evidence.get("actual_small_live_lifecycle_evidence") is False:
        return False, "SMALL_LIFECYCLE_FAIL"

    return True, "ADMISSIBLE"


def select_relative_tier_pool(candidates, account_mode, target_tier, avg_equity_usdt=None, timeframe=None):
    """Relative competition after a safety floor; maxima_are_not_quotas."""
    tier = str(target_tier or "").upper()
    maximum = TIER_MAXIMA.get(tier)
    if maximum is None:
        return {"ok": False, "decision": "BLOCK", "reason": "TARGET_TIER_UNKNOWN", "selected": []}

    tf = str(timeframe or "").upper()
    admissible = []
    rejected = []
    for row in candidates if isinstance(candidates, list) else []:
        if not isinstance(row, dict):
            continue
        if tf and str(row.get("timeframe") or "").upper() not in {"", tf}:
            continue
        ok, reason = _survival_admissible(row, tier)
        if ok:
            admissible.append(row)
        else:
            rejected.append({
                "strategy": row.get("strategy"),
                "strategy_version": row.get("strategy_version"),
                "reason": reason,
            })

    ranking = rank_strategies(admissible, account_mode, avg_equity_usdt)
    if not ranking.get("ok"):
        return {
            "ok": False,
            "decision": "BLOCK",
            "reason": ranking.get("reason") or "NO_ADMISSIBLE_STRATEGY",
            "target_tier": tier,
            "tier_maximum": maximum,
            "maxima_are_not_quotas": True,
            "selected": [],
            "rejected": rejected,
            "ranking": ranking,
        }

    selected = list(ranking.get("ranked") or [])[:maximum]
    return {
        "ok": bool(selected),
        "decision": "PASS" if selected else "BLOCK",
        "reason": "RELATIVE_POOL_SELECTED" if selected else "NO_ADMISSIBLE_STRATEGY",
        "target_tier": tier,
        "timeframe": tf,
        "tier_maximum": maximum,
        "maxima_are_not_quotas": True,
        "forced_fill": False,
        "selected": selected,
        "rejected": rejected,
        "ranking": ranking,
    }
