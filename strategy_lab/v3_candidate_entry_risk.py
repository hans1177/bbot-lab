# ???: strategy_lab/v3_candidate_entry_risk.py
"""Candidate-level predictive Entry Risk research/shadow contract.

This module is deliberately NOT a LIVE policy implementation.

Change-control stage:
    Constitution
    -> Verifier
    -> research/shadow implementation
    -> predicted-vs-realized calibration
    -> evidence validation
    -> validated Entry Risk Policy Version
    -> CANARY / SMALL / NORMAL

Until a separately validated Entry Risk Policy Version exists:

* effect_mode is SHADOW only.
* no candidate may be removed or reordered by this module.
* no qty or leverage may be changed by this module.
* historical prior is not converted into a prediction.
* final LIVE Cross-Stress Entry Risk guard remains the existing
  risk_engine.py:v3_entry_risk_contract path.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any


SCHEMA = (
    "B_BOT_V3_CANDIDATE_ENTRY_RISK_V1"
)

DEFAULT_POLICY_VERSION = (
    "PV_ENTRY_RISK_BASELINE_V1"
)

EFFECT_MODE_SHADOW = "SHADOW"

CALIBRATION_STATUS = (
    "NOT_PREDICTED_VS_REALIZED_CALIBRATED"
)

POLICY_STATUS = (
    "RESEARCH_SHADOW_UNVALIDATED"
)

LIVE_EFFECT = False

HISTORICAL_PRIOR_USED = False


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None

    if not math.isfinite(number):
        return None

    return number


def _text(value: Any) -> str:
    if value is None:
        return ""

    return str(value).strip()


def _upper(value: Any) -> str:
    return _text(value).upper()


def _candidate_identity(
    candidate: dict[str, Any],
) -> dict[str, str]:
    strategy = (
        _text(
            candidate.get(
                "strategy_id"
            )
        )
        or
        _text(
            candidate.get(
                "strategy"
            )
        )
    )

    timeframe = (
        _upper(
            candidate.get(
                "timeframe"
            )
        )
        or
        _upper(
            candidate.get(
                "frame"
            )
        )
    )

    return {
        "symbol":
            _upper(
                candidate.get(
                    "symbol"
                )
            ),

        "side":
            _upper(
                candidate.get(
                    "side"
                )
            ),

        "timeframe":
            timeframe,

        "strategy":
            strategy,

        "strategy_version":
            _text(
                candidate.get(
                    "strategy_version"
                )
            ),
    }


def _late_entry_block(
    candidate: dict[str, Any],
) -> dict[str, Any]:
    value = candidate.get(
        "late_entry"
    )

    if isinstance(value, dict):
        return value

    return {}


def _signal_block(
    candidate: dict[str, Any],
) -> dict[str, Any]:
    value = candidate.get(
        "signal"
    )

    if isinstance(value, dict):
        return value

    return {}


def validate_candidate_entry_risk_input(
    candidate: Any,
    current_price: Any,
) -> dict[str, Any]:
    """Validate research/shadow Entry Risk inputs.

    This is intentionally stricter than a loose telemetry collector.

    Candidate Entry Risk sits AFTER Late Entry Replay, therefore a
    candidate without an explicit replay result is incomplete evidence.
    No LIVE mutation occurs; invalid input is returned as a fail-closed
    SHADOW observation.
    """

    errors: list[str] = []

    if not isinstance(
        candidate,
        dict,
    ):
        return {
            "ok": False,
            "errors": [
                "CANDIDATE_NOT_OBJECT"
            ],
            "identity": {
                "symbol": "",
                "side": "",
                "timeframe": "",
                "strategy": "",
                "strategy_version": "",
            },
            "current_price": None,
            "late_entry_decision": "",
        }

    identity = _candidate_identity(
        candidate
    )

    if not identity["symbol"]:
        errors.append(
            "SYMBOL_MISSING"
        )

    if identity["side"] not in {
        "LONG",
        "SHORT",
    }:
        errors.append(
            "SIDE_INVALID"
        )

    if not identity["timeframe"]:
        errors.append(
            "TIMEFRAME_MISSING"
        )

    if not identity["strategy"]:
        errors.append(
            "STRATEGY_MISSING"
        )

    price = _finite(
        current_price
    )

    if (
        price is None
        or price <= 0.0
    ):
        errors.append(
            "CURRENT_PRICE_INVALID"
        )

    late_entry = (
        _late_entry_block(
            candidate
        )
    )

    late_decision = _upper(
        late_entry.get(
            "decision"
        )
    )

    if not late_entry:
        errors.append(
            "LATE_ENTRY_REPLAY_MISSING"
        )

    elif not late_decision:
        errors.append(
            "LATE_ENTRY_DECISION_MISSING"
        )

    elif late_decision in {
        "UNKNOWN",
        "ERROR",
        "INVALID",
    }:
        errors.append(
            "LATE_ENTRY_REPLAY_AMBIGUOUS"
        )

    return {
        "ok":
            not errors,

        "errors":
            errors,

        "identity":
            identity,

        "current_price":
            price,

        "late_entry_decision":
            late_decision,
    }


def _observed_features(
    candidate: dict[str, Any],
    current_price: float | None,
) -> dict[str, Any]:
    signal = _signal_block(
        candidate
    )

    late_entry = _late_entry_block(
        candidate
    )

    signal_price = _finite(
        signal.get(
            "signal_price"
        )
    )

    if signal_price is None:
        signal_price = _finite(
            candidate.get(
                "signal_price"
            )
        )

    distance_pct = None

    if (
        current_price is not None
        and
        signal_price is not None
        and
        signal_price > 0.0
    ):
        distance_pct = (
            (
                current_price
                - signal_price
            )
            / signal_price
            * 100.0
        )

    remaining_edge = _finite(
        late_entry.get(
            "remaining_expected_edge"
        )
    )

    return {
        "current_price":
            current_price,

        "signal_price":
            signal_price,

        "signal_distance_pct":
            distance_pct,

        "late_entry_decision":
            _upper(
                late_entry.get(
                    "decision"
                )
            ),

        "remaining_expected_edge":
            remaining_edge,
    }


def _base_contract(
    *,
    policy_version: str,
    requested_effect_mode: str,
    validation: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    identity = (
        validation.get(
            "identity"
        )
        if isinstance(
            validation.get(
                "identity"
            ),
            dict,
        )
        else {}
    )

    current_price = _finite(
        validation.get(
            "current_price"
        )
    )

    return {
        "schema":
            SCHEMA,

        "policy_version":
            policy_version,

        "policy_status":
            POLICY_STATUS,

        # Only SHADOW is supported at this stage.
        "effect_mode":
            EFFECT_MODE_SHADOW,

        "requested_effect_mode":
            requested_effect_mode,

        # OBSERVE is deliberate: there is no calibrated predictive
        # ALLOW/REDUCE/BLOCK policy yet.
        "decision":
            "OBSERVE",

        "entry_risk_score":
            None,

        "stop_hit_probability":
            None,

        "expected_adverse_move":
            None,

        "safe_size_qty":
            None,

        "safe_leverage":
            None,

        "calibration_status":
            CALIBRATION_STATUS,

        "reason":
            (
                "SHADOW_ONLY_NO_VALIDATED_PREDICTIVE_POLICY"
            ),

        "live_effect":
            LIVE_EFFECT,

        "validated_policy":
            False,

        "candidate_block_effect":
            False,

        "candidate_rank_effect":
            False,

        "qty_effect":
            False,

        "leverage_effect":
            False,

        "historical_prior_used":
            HISTORICAL_PRIOR_USED,

        "candidate_identity":
            identity,

        "observed_features":
            _observed_features(
                candidate,
                current_price,
            ),
    }


def evaluate_candidate_entry_risk(
    candidate: Any,
    current_price: Any,
    *,
    policy_version: str = DEFAULT_POLICY_VERSION,
    effect_mode: str = EFFECT_MODE_SHADOW,
) -> dict[str, Any]:
    """Produce a no-LIVE-effect Candidate Entry Risk shadow result.

    Numeric predictive fields intentionally remain None until a
    predicted-vs-realized calibration and evidence-validation phase
    creates a separately validated Entry Risk Policy Version.
    """

    requested_effect_mode = _upper(
        effect_mode
    )

    policy_version = (
        _text(
            policy_version
        )
        or DEFAULT_POLICY_VERSION
    )

    candidate_obj = (
        candidate
        if isinstance(
            candidate,
            dict,
        )
        else {}
    )

    validation = (
        validate_candidate_entry_risk_input(
            candidate,
            current_price,
        )
    )

    result = _base_contract(
        policy_version=
            policy_version,

        requested_effect_mode=
            requested_effect_mode,

        validation=
            validation,

        candidate=
            candidate_obj,
    )

    result[
        "input_valid"
    ] = bool(
        validation.get(
            "ok"
        )
    )

    result[
        "input_errors"
    ] = list(
        validation.get(
            "errors"
        )
        or []
    )

    # Any request for non-SHADOW effect fails closed.
    if (
        requested_effect_mode
        != EFFECT_MODE_SHADOW
    ):
        result[
            "decision"
        ] = "BLOCK"

        result[
            "reason"
        ] = (
            "UNVALIDATED_ENTRY_RISK_LIVE_EFFECT_FORBIDDEN"
        )

        result[
            "input_valid"
        ] = False

        result[
            "input_errors"
        ].append(
            "EFFECT_MODE_NOT_SHADOW"
        )

        return result

    # C6-02 requires Late Entry Replay evidence first.
    # Invalid/ambiguous inputs are represented as a SHADOW block,
    # not as a LIVE candidate-removal action.
    if not result[
        "input_valid"
    ]:
        result[
            "decision"
        ] = "BLOCK"

        result[
            "reason"
        ] = (
            "SHADOW_INPUT_FAIL_CLOSED"
        )

        return result

    return result


def candidate_entry_risk_shadow_record(
    candidate: Any,
    current_price: Any,
    *,
    policy_version: str = DEFAULT_POLICY_VERSION,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Build a pure research telemetry record.

    This function does not write files, mutate the candidate, or alter
    LIVE policy.  It only returns a serializable observation payload.
    """

    result = (
        evaluate_candidate_entry_risk(
            candidate,
            current_price,
            policy_version=
                policy_version,
            effect_mode=
                EFFECT_MODE_SHADOW,
        )
    )

    if observed_at is None:
        observed_at = (
            datetime.now(
                timezone.utc
            ).isoformat()
        )

    return {
        "schema":
            "B_BOT_V3_CANDIDATE_ENTRY_RISK_SHADOW_RECORD_V1",

        "observed_at":
            observed_at,

        "effect_mode":
            "SHADOW",

        "live_effect":
            False,

        "entry_risk":
            result,
    }


__all__ = [
    "SCHEMA",
    "DEFAULT_POLICY_VERSION",
    "EFFECT_MODE_SHADOW",
    "CALIBRATION_STATUS",
    "validate_candidate_entry_risk_input",
    "evaluate_candidate_entry_risk",
    "candidate_entry_risk_shadow_record",
]
