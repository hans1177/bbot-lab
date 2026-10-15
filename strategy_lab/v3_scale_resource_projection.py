# filename: v3_scale_resource_projection.py

"""
GREEN7A_SCALE_RESOURCE_PROJECTION_V1

Pure projection only.

R6 semantics preserved:

    projected_initial_margin
        = existing_initial_margin
        + candidate_initial_margin

    projected_initial_margin_ratio
        = projected_initial_margin
        / current_margin_balance

No cap is invented here.
If validated numeric policy does not provide a cap,
positive scale fails closed.
"""

from __future__ import annotations

import math


MARKER = "GREEN7A_SCALE_RESOURCE_PROJECTION_V1"


def _finite_nonnegative(value):
    try:
        x = float(value)
    except Exception:
        return None

    if (
        not math.isfinite(x)
        or x < 0.0
    ):
        return None

    return x


def _finite_positive(value):
    x = _finite_nonnegative(value)

    if (
        x is None
        or x <= 0.0
    ):
        return None

    return x


def _valid_cap(value):
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


def project_initial_margin(
    *,
    existing_initial_margin,
    candidate_initial_margin,
    current_margin_balance,
    portfolio_initial_margin_cap_ratio,
):
    """
    R6-compatible projection.

    This function never infers available margin and never creates
    a portfolio cap.
    """

    existing = _finite_nonnegative(
        existing_initial_margin
    )

    candidate = _finite_nonnegative(
        candidate_initial_margin
    )

    balance = _finite_positive(
        current_margin_balance
    )

    if existing is None:
        return {
            "schema": MARKER,
            "ok": False,
            "decision": "BLOCK",
            "reason":
                "EXISTING_INITIAL_MARGIN_INVALID",
        }

    if candidate is None:
        return {
            "schema": MARKER,
            "ok": False,
            "decision": "BLOCK",
            "reason":
                "CANDIDATE_INITIAL_MARGIN_INVALID",
        }

    if balance is None:
        return {
            "schema": MARKER,
            "ok": False,
            "decision": "BLOCK",
            "reason":
                "CURRENT_MARGIN_BALANCE_INVALID",
        }

    cap = _valid_cap(
        portfolio_initial_margin_cap_ratio
    )

    projected = (
        existing
        + candidate
    )

    ratio = (
        projected
        / balance
    )

    if cap is None:
        return {
            "schema": MARKER,
            "ok": False,
            "decision":
                "BLOCK_POSITIVE_SCALE",
            "reason":
                "NUMERIC_POLICY_MISSING",
            "existing_initial_margin":
                existing,
            "candidate_initial_margin":
                candidate,
            "current_margin_balance":
                balance,
            "projected_initial_margin":
                projected,
            "projected_initial_margin_ratio":
                ratio,
            "portfolio_initial_margin_cap_ratio":
                None,
            "r6_formula_reused":
                True,
            "available_margin_inferred":
                False,
        }

    passed = (
        ratio <= cap
    )

    return {
        "schema": MARKER,

        "ok":
            bool(passed),

        "decision":
            (
                "PASS"
                if passed
                else "BLOCK"
            ),

        "reason":
            (
                "PROJECTED_INITIAL_MARGIN_WITHIN_CAP"
                if passed
                else "PROJECTED_INITIAL_MARGIN_CAP_EXCEEDED"
            ),

        "existing_initial_margin":
            existing,

        "candidate_initial_margin":
            candidate,

        "current_margin_balance":
            balance,

        "projected_initial_margin":
            projected,

        "projected_initial_margin_ratio":
            ratio,

        "portfolio_initial_margin_cap_ratio":
            cap,

        "r6_formula_reused":
            True,

        "available_margin_inferred":
            False,
    }


def aggregate_existing_initial_margin(
    positions,
):
    """
    Read-only aggregation helper.

    Accepts either exchange-style initialMargin /
    positionInitialMargin or normalized initial_margin.
    """

    if not isinstance(
        positions,
        dict,
    ):
        return {
            "ok": False,
            "reason":
                "POSITIONS_INVALID",
            "total_initial_margin":
                None,
        }

    total = 0.0
    active = 0

    for row in positions.values():

        if not isinstance(
            row,
            dict,
        ):
            continue

        raw_amt = (
            row.get("positionAmt")
            if "positionAmt" in row
            else row.get(
                "position_amt"
            )
        )

        try:
            amt = float(
                raw_amt
                or 0.0
            )
        except Exception:
            return {
                "ok": False,
                "reason":
                    "POSITION_AMOUNT_INVALID",
                "total_initial_margin":
                    None,
            }

        if abs(amt) <= 0.0:
            continue

        raw_margin = (
            row.get(
                "initial_margin"
            )
            if "initial_margin" in row
            else (
                row.get(
                    "initialMargin"
                )
                if "initialMargin" in row
                else row.get(
                    "positionInitialMargin"
                )
            )
        )

        margin = _finite_nonnegative(
            raw_margin
        )

        if margin is None:
            return {
                "ok": False,
                "reason":
                    "POSITION_INITIAL_MARGIN_INVALID",
                "total_initial_margin":
                    None,
            }

        total += margin
        active += 1

    return {
        "ok": True,
        "reason": "OK",
        "active_positions": active,
        "total_initial_margin": total,
    }
