from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

import v3_ai_entry_grammar as grammar


SCHEMA = "B_BOT_AI_ENTRY_INTERPRETER_V1"

# V1 deliberately supports only primitives whose semantics are already
# established by existing B-BOT research code.
SUPPORTED_PRIMITIVES = frozenset({
    "EFFICIENCY",
    "MOMENTUM",
})

FAIL_CLOSED_PRIMITIVES = frozenset(
    grammar.ALLOWED_PRIMITIVES
    - SUPPORTED_PRIMITIVES
)


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series,
        errors="coerce",
    )


def _resolve_threshold(
    condition: dict[str, Any],
    parameter_values: dict[str, Any],
) -> float:

    if "value" in condition:
        value = condition["value"]
    else:
        name = condition.get("parameter")

        if (
            not isinstance(name, str)
            or name not in parameter_values
        ):
            raise RuntimeError(
                "AI_ENTRY_PARAMETER_VALUE_MISSING"
            )

        value = parameter_values[name]

    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
    ):
        raise RuntimeError(
            "AI_ENTRY_THRESHOLD_NOT_NUMERIC"
        )

    return float(value)


def _primitive_series(
    df: pd.DataFrame,
    condition: dict[str, Any],
) -> pd.Series:

    primitive = str(
        condition.get("primitive") or ""
    ).upper()

    if primitive not in SUPPORTED_PRIMITIVES:
        raise RuntimeError(
            "AI_ENTRY_PRIMITIVE_UNSUPPORTED_V1:"
            + primitive
        )

    window = condition.get("window")

    if (
        not isinstance(window, int)
        or isinstance(window, bool)
        or window <= 0
    ):
        raise RuntimeError(
            "AI_ENTRY_WINDOW_REQUIRED"
        )

    close = _num(df["close"])

    if primitive == "EFFICIENCY":

        # Existing B-BOT Kaufman-style efficiency ratio:
        # abs(C[t]-C[t-window]) /
        # sum(abs(diff(C)), window)
        change = (
            close
            - close.shift(window)
        ).abs()

        path = (
            close.diff()
            .abs()
            .rolling(
                window,
                min_periods=window,
            )
            .sum()
            .replace(0, np.nan)
        )

        return change / path

    if primitive == "MOMENTUM":

        # Existing B-BOT directional close displacement.
        return (
            close
            - close.shift(window)
        )

    raise RuntimeError(
        "AI_ENTRY_PRIMITIVE_DISPATCH_FAIL"
    )


def _compare(
    series: pd.Series,
    operator: str,
    threshold: float,
) -> pd.Series:

    op = str(operator or "").upper()

    if op == "GT":
        return series > threshold

    if op == "LT":
        return series < threshold

    if op == "GTE":
        return series >= threshold

    if op == "LTE":
        return series <= threshold

    if op == "CROSS_ABOVE":
        return (
            (series > threshold)
            & (
                series.shift(1)
                <= threshold
            )
        )

    if op == "CROSS_BELOW":
        return (
            (series < threshold)
            & (
                series.shift(1)
                >= threshold
            )
        )

    raise RuntimeError(
        "AI_ENTRY_OPERATOR_UNSUPPORTED:"
        + op
    )


def _condition_mask(
    df: pd.DataFrame,
    condition: dict[str, Any],
    parameter_values: dict[str, Any],
) -> pd.Series:

    series = _primitive_series(
        df,
        condition,
    )

    threshold = _resolve_threshold(
        condition,
        parameter_values,
    )

    return _compare(
        series,
        condition["operator"],
        threshold,
    )


def _rule_mask(
    df: pd.DataFrame,
    rule: dict[str, Any],
    parameter_values: dict[str, Any],
) -> pd.Series:

    conditions = rule["conditions"]

    masks = [
        _condition_mask(
            df,
            condition,
            parameter_values,
        )
        for condition in conditions
    ]

    if not masks:
        raise RuntimeError(
            "AI_ENTRY_EMPTY_RULE"
        )

    logic = str(
        rule.get("logic") or ""
    ).upper()

    result = masks[0]

    if logic == "AND":
        for mask in masks[1:]:
            result = result & mask

    elif logic == "OR":
        for mask in masks[1:]:
            result = result | mask

    else:
        raise RuntimeError(
            "AI_ENTRY_LOGIC_UNSUPPORTED:"
            + logic
        )

    return result.fillna(False)


def validate_execution_contract(
    proposal: dict[str, Any],
    parameter_values: dict[str, Any],
) -> dict[str, Any]:

    base = grammar.validate_proposal(
        proposal
    )

    errors = list(
        base.get("errors") or []
    )

    if not base.get("valid"):
        return {
            "valid": False,
            "errors": sorted(set(errors)),
        }

    used = set()

    for key in (
        "long_rule",
        "short_rule",
    ):
        rule = proposal.get(key) or {}

        for condition in (
            rule.get("conditions") or []
        ):
            primitive = str(
                condition.get("primitive")
                or ""
            ).upper()

            used.add(primitive)

            if (
                primitive
                not in SUPPORTED_PRIMITIVES
            ):
                errors.append(
                    "EXECUTION_PRIMITIVE_UNSUPPORTED:"
                    + primitive
                )

    domain = (
        proposal.get(
            "finite_parameter_domain"
        )
        or {}
    )

    for name in domain:
        if name not in parameter_values:
            errors.append(
                "CONCRETE_PARAMETER_MISSING:"
                + str(name)
            )

    for name in parameter_values:
        if name not in domain:
            errors.append(
                "CONCRETE_PARAMETER_NOT_IN_DOMAIN:"
                + str(name)
            )

    for name, value in parameter_values.items():

        allowed = domain.get(name)

        if (
            not isinstance(allowed, list)
            or value not in allowed
        ):
            errors.append(
                "CONCRETE_PARAMETER_OUT_OF_DOMAIN:"
                + str(name)
            )

    return {
        "valid": not errors,
        "errors": sorted(set(errors)),
        "used_primitives": sorted(used),
    }


def generate_signals(
    df: pd.DataFrame,
    proposal: dict[str, Any],
    parameter_values: dict[str, Any],
) -> np.ndarray:

    check = validate_execution_contract(
        proposal,
        parameter_values,
    )

    if not check["valid"]:
        raise RuntimeError(
            "AI_ENTRY_EXECUTION_CONTRACT_FAIL:"
            + "|".join(check["errors"])
        )

    long_mask = _rule_mask(
        df,
        proposal["long_rule"],
        parameter_values,
    )

    short_mask = _rule_mask(
        df,
        proposal["short_rule"],
        parameter_values,
    )

    overlap = (
        long_mask
        & short_mask
    )

    # Ambiguous same-bar long+short is fail-neutral.
    long_mask = (
        long_mask
        & ~overlap
    )

    short_mask = (
        short_mask
        & ~overlap
    )

    out = np.zeros(
        len(df),
        dtype=np.int8,
    )

    out[
        long_mask.to_numpy()
    ] = 1

    out[
        short_mask.to_numpy()
    ] = -1

    return out


def self_test() -> None:

    proposal = {
        "research_question":
            "Can efficiency-conditioned momentum improve entries?",
        "hypothesis_name":
            "INTERPRETER_SELF_TEST",
        "rationale":
            "Bounded deterministic test.",
        "novelty_basis":
            "Execution interpreter test.",
        "expected_failure_modes": [
            "CHOP_REGIME"
        ],
        "lookahead_risk":
            "Closed-bar only.",
        "evidence_status":
            "UNTESTED",
        "decision":
            "HYPOTHESIS_ONLY",
        "finite_parameter_domain": {
            "eff_threshold": [0.35],
            "mom_threshold": [0.0],
        },
        "long_rule": {
            "side": "LONG",
            "logic": "AND",
            "conditions": [
                {
                    "primitive": "EFFICIENCY",
                    "operator": "GT",
                    "window": 20,
                    "parameter": "eff_threshold",
                },
                {
                    "primitive": "MOMENTUM",
                    "operator": "GT",
                    "window": 3,
                    "parameter": "mom_threshold",
                },
            ],
        },
        "short_rule": {
            "side": "SHORT",
            "logic": "AND",
            "conditions": [
                {
                    "primitive": "EFFICIENCY",
                    "operator": "GT",
                    "window": 20,
                    "parameter": "eff_threshold",
                },
                {
                    "primitive": "MOMENTUM",
                    "operator": "LT",
                    "window": 3,
                    "parameter": "mom_threshold",
                },
            ],
        },
    }

    df = pd.DataFrame({
        "close": [
            100.0 + i
            for i in range(60)
        ],
    })

    parameters = {
        "eff_threshold": 0.35,
        "mom_threshold": 0.0,
    }

    check = validate_execution_contract(
        proposal,
        parameters,
    )

    assert check["valid"], check

    a = generate_signals(
        df,
        proposal,
        parameters,
    )

    b = generate_signals(
        df,
        proposal,
        parameters,
    )

    assert np.array_equal(a, b)
    assert len(a) == len(df)
    assert set(np.unique(a)).issubset(
        {-1, 0, 1}
    )

    unsupported = dict(proposal)
    unsupported["long_rule"] = {
        "side": "LONG",
        "logic": "AND",
        "conditions": [
            {
                "primitive": "ATR",
                "operator": "GT",
                "window": 14,
                "value": 1.0,
            }
        ],
    }

    bad = validate_execution_contract(
        unsupported,
        parameters,
    )

    assert not bad["valid"]

    print("SCHEMA=", SCHEMA)
    print("SELF_TEST=PASS")
    print(
        "SUPPORTED_PRIMITIVES=",
        ",".join(
            sorted(
                SUPPORTED_PRIMITIVES
            )
        ),
    )
    print("UNSUPPORTED_FAIL_CLOSED=PASS")
    print("DETERMINISTIC=PASS")
    print("ARBITRARY_CODE_EXECUTION=NO")
    print("RESEARCH_EXECUTION=NO")
    print("HARD_PASS_EXECUTION=NO")
    print("KNOWLEDGE_WRITE=NO")
    print("ORDER_WRITE=NO")


if __name__ == "__main__":
    self_test()
