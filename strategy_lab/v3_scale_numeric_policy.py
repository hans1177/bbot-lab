# filename: v3_scale_numeric_policy.py

"""
GREEN7A_VALIDATED_NUMERIC_POLICY_V1

Pure numeric-policy validation layer.

Rules:
- Never invent portfolio/risk numeric thresholds.
- Missing numeric evidence fails closed.
- A value is usable only when its declared source is validated.
- Tier capital weights are Constitution-defined bootstrap ceilings,
  not substitutes for portfolio safety thresholds.
"""

from __future__ import annotations

import math


MARKER = "GREEN7A_VALIDATED_NUMERIC_POLICY_V1"

TIER_WEIGHTS = {
    "CANARY": 0.20,
    "SMALL": 0.50,
    "NORMAL": 1.00,
}

REQUIRED_NUMERIC_KEYS = (
    "portfolio_initial_margin_cap_ratio",
    "margin_safety_reserve_ratio",
    "symbol_concentration_cap_ratio",
    "strategy_concentration_cap_ratio",
    "correlation_concentration_cap_ratio",
    "account_risk_budget_ratio",
)


def _finite_ratio(value):
    try:
        x = float(value)
    except Exception:
        return None

    if (
        not math.isfinite(x)
        or x <= 0.0
        or x > 1.0
    ):
        return None

    return x


def _validated_source(row):
    if not isinstance(row, dict):
        return False

    return (
        row.get("validated") is True
        and bool(
            str(
                row.get("policy_version")
                or ""
            ).strip()
        )
    )


def validated_numeric_policy(
    policy_evidence,
):
    """
    Validate externally produced policy evidence.

    Expected input shape:

    {
        "<numeric_key>": {
            "value": <ratio>,
            "validated": True,
            "policy_version": "PV_..."
        }
    }

    Missing/invalid/unvalidated values are not substituted.
    """

    src = (
        policy_evidence
        if isinstance(
            policy_evidence,
            dict,
        )
        else {}
    )

    values = {}
    missing = []
    invalid = []

    for key in REQUIRED_NUMERIC_KEYS:

        row = src.get(key)

        if not isinstance(row, dict):
            missing.append(key)
            continue

        if not _validated_source(row):
            invalid.append(
                key + ":SOURCE_NOT_VALIDATED"
            )
            continue

        value = _finite_ratio(
            row.get("value")
        )

        if value is None:
            invalid.append(
                key + ":INVALID_RATIO"
            )
            continue

        values[key] = value

    ok = (
        not missing
        and not invalid
        and len(values)
        == len(REQUIRED_NUMERIC_KEYS)
    )

    return {
        "schema":
            MARKER,

        "ok":
            bool(ok),

        "decision":
            (
                "PASS"
                if ok
                else "BLOCK_POSITIVE_SCALE"
            ),

        "values":
            values,

        "missing_keys":
            missing,

        "invalid_keys":
            invalid,

        "hardcoded_fallback_used":
            False,

        "unvalidated_threshold_invention":
            False,

        "tier_weights":
            dict(TIER_WEIGHTS),
    }


def tier_weight(tier):
    return TIER_WEIGHTS.get(
        str(tier or "").upper()
    )
