from __future__ import annotations

import hashlib
import json
from typing import Any


SCHEMA = "B_BOT_AI_ENTRY_GRAMMAR_V1"

# AI가 사용할 수 있는 진입 primitive.
# 이것은 실행 코드가 아니라 선언형 연구 표현이다.
ALLOWED_PRIMITIVES = frozenset({
    "RETURN",
    "EMA",
    "ATR",
    "RANGE",
    "VOLUME_RATIO",
    "EFFICIENCY",
    "MOMENTUM",
    "BREAKOUT",
})

ALLOWED_OPERATORS = frozenset({
    "GT",
    "LT",
    "GTE",
    "LTE",
    "CROSS_ABOVE",
    "CROSS_BELOW",
})

ALLOWED_LOGICAL_OPERATORS = frozenset({
    "AND",
    "OR",
})

ALLOWED_SIDES = frozenset({
    "LONG",
    "SHORT",
})

MAX_CONDITIONS_PER_SIDE = 6
MAX_PARAMETERS = 8
MAX_VALUES_PER_PARAMETER = 12

# 파라미터 폭주 방지용. 전략 성능 기준이 아니다.
ABS_NUMERIC_LIMIT = 1000000.0


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
    )


def _canonical_number(value: Any) -> Any:
    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        if value == 0:
            return 0

        if value.is_integer():
            return int(value)

        return float(format(value, ".12g"))

    return value


def _validate_scalar(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip()) and len(value) <= 64

    if _is_number(value):
        return abs(float(value)) <= ABS_NUMERIC_LIMIT

    return False


def validate_parameter_domain(
    domain: Any,
) -> list[str]:

    errors: list[str] = []

    if not isinstance(domain, dict) or not domain:
        return ["FINITE_PARAMETER_DOMAIN_REQUIRED"]

    if len(domain) > MAX_PARAMETERS:
        errors.append("TOO_MANY_PARAMETERS")

    for key, values in domain.items():

        if not isinstance(key, str) or not key.strip():
            errors.append("INVALID_PARAMETER_NAME")
            continue

        if len(key) > 64:
            errors.append(
                f"PARAMETER_NAME_TOO_LONG:{key[:32]}"
            )

        if not isinstance(values, list) or not values:
            errors.append(
                f"EMPTY_PARAMETER_VALUES:{key}"
            )
            continue

        if len(values) > MAX_VALUES_PER_PARAMETER:
            errors.append(
                f"TOO_MANY_PARAMETER_VALUES:{key}"
            )

        canonical_values = []

        for value in values:
            if not _validate_scalar(value):
                errors.append(
                    f"INVALID_PARAMETER_VALUE:{key}"
                )
                continue

            canonical_values.append(
                _canonical_number(value)
            )

        fingerprints = {
            json.dumps(
                value,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            for value in canonical_values
        }

        if (
            canonical_values
            and len(fingerprints) != len(canonical_values)
        ):
            errors.append(
                f"DUPLICATE_PARAMETER_VALUE:{key}"
            )

    return errors


def validate_condition(
    condition: Any,
    side: str,
    index: int,
) -> list[str]:

    prefix = f"{side}_CONDITION_{index}"
    errors: list[str] = []

    if not isinstance(condition, dict):
        return [f"{prefix}:NOT_OBJECT"]

    allowed_keys = {
        "primitive",
        "operator",
        "window",
        "value",
        "parameter",
    }

    unknown = set(condition) - allowed_keys

    if unknown:
        errors.append(
            f"{prefix}:UNKNOWN_KEYS:"
            + ",".join(sorted(unknown))
        )

    primitive = condition.get("primitive")
    operator = condition.get("operator")

    if primitive not in ALLOWED_PRIMITIVES:
        errors.append(
            f"{prefix}:INVALID_PRIMITIVE"
        )

    if operator not in ALLOWED_OPERATORS:
        errors.append(
            f"{prefix}:INVALID_OPERATOR"
        )

    window = condition.get("window")

    if window is not None:
        if (
            not isinstance(window, int)
            or isinstance(window, bool)
            or window <= 0
            or window > 10000
        ):
            errors.append(
                f"{prefix}:INVALID_WINDOW"
            )

    has_value = "value" in condition
    has_parameter = "parameter" in condition

    if has_value == has_parameter:
        errors.append(
            f"{prefix}:REQUIRE_EXACTLY_ONE_OF_VALUE_OR_PARAMETER"
        )

    if has_value:
        if not _validate_scalar(condition["value"]):
            errors.append(
                f"{prefix}:INVALID_VALUE"
            )

    if has_parameter:
        parameter = condition["parameter"]

        if (
            not isinstance(parameter, str)
            or not parameter.strip()
            or len(parameter) > 64
        ):
            errors.append(
                f"{prefix}:INVALID_PARAMETER_REFERENCE"
            )

    return errors


def validate_side_rule(
    rule: Any,
    expected_side: str,
) -> list[str]:

    errors: list[str] = []

    if not isinstance(rule, dict):
        return [f"{expected_side}_RULE_NOT_OBJECT"]

    allowed_keys = {
        "side",
        "logic",
        "conditions",
    }

    unknown = set(rule) - allowed_keys

    if unknown:
        errors.append(
            f"{expected_side}_RULE_UNKNOWN_KEYS:"
            + ",".join(sorted(unknown))
        )

    if rule.get("side") != expected_side:
        errors.append(
            f"{expected_side}_SIDE_MISMATCH"
        )

    if rule.get("logic") not in ALLOWED_LOGICAL_OPERATORS:
        errors.append(
            f"{expected_side}_INVALID_LOGIC"
        )

    conditions = rule.get("conditions")

    if not isinstance(conditions, list) or not conditions:
        errors.append(
            f"{expected_side}_CONDITIONS_REQUIRED"
        )
        return errors

    if len(conditions) > MAX_CONDITIONS_PER_SIDE:
        errors.append(
            f"{expected_side}_TOO_MANY_CONDITIONS"
        )

    for index, condition in enumerate(conditions):
        errors.extend(
            validate_condition(
                condition,
                expected_side,
                index,
            )
        )

    return errors


def _parameter_references(
    proposal: dict[str, Any],
) -> set[str]:

    refs: set[str] = set()

    for key in ("long_rule", "short_rule"):
        rule = proposal.get(key)

        if not isinstance(rule, dict):
            continue

        conditions = rule.get("conditions")

        if not isinstance(conditions, list):
            continue

        for condition in conditions:
            if not isinstance(condition, dict):
                continue

            parameter = condition.get("parameter")

            if isinstance(parameter, str):
                refs.add(parameter)

    return refs


def validate_proposal(
    proposal: Any,
) -> dict[str, Any]:

    errors: list[str] = []

    if not isinstance(proposal, dict):
        return {
            "valid": False,
            "errors": ["PROPOSAL_NOT_OBJECT"],
        }

    required_metadata = (
        "research_question",
        "hypothesis_name",
        "rationale",
        "novelty_basis",
        "expected_failure_modes",
        "lookahead_risk",
        "evidence_status",
        "decision",
        "finite_parameter_domain",
        "long_rule",
        "short_rule",
    )

    for key in required_metadata:
        if key not in proposal:
            errors.append(f"MISSING_{key.upper()}")

    for key in (
        "research_question",
        "hypothesis_name",
        "rationale",
        "novelty_basis",
        "lookahead_risk",
    ):
        value = proposal.get(key)

        if (
            not isinstance(value, str)
            or not value.strip()
        ):
            errors.append(
                f"INVALID_{key.upper()}"
            )

    if proposal.get("evidence_status") != "UNTESTED":
        errors.append(
            "EVIDENCE_STATUS_MUST_BE_UNTESTED"
        )

    if proposal.get("decision") != "HYPOTHESIS_ONLY":
        errors.append(
            "DECISION_MUST_BE_HYPOTHESIS_ONLY"
        )

    failure_modes = proposal.get(
        "expected_failure_modes"
    )

    if (
        not isinstance(failure_modes, list)
        or not failure_modes
        or not all(
            isinstance(x, str) and x.strip()
            for x in failure_modes
        )
    ):
        errors.append(
            "EXPECTED_FAILURE_MODES_REQUIRED"
        )

    domain = proposal.get(
        "finite_parameter_domain"
    )

    errors.extend(
        validate_parameter_domain(domain)
    )

    errors.extend(
        validate_side_rule(
            proposal.get("long_rule"),
            "LONG",
        )
    )

    errors.extend(
        validate_side_rule(
            proposal.get("short_rule"),
            "SHORT",
        )
    )

    if isinstance(domain, dict):
        refs = _parameter_references(proposal)
        defined = set(domain)

        missing = refs - defined
        unused = defined - refs

        for name in sorted(missing):
            errors.append(
                f"UNDEFINED_PARAMETER_REFERENCE:{name}"
            )

        for name in sorted(unused):
            errors.append(
                f"UNUSED_PARAMETER:{name}"
            )

    return {
        "valid": not errors,
        "errors": sorted(set(errors)),
    }


def canonical_hypothesis(
    proposal: dict[str, Any],
) -> dict[str, Any]:

    validation = validate_proposal(proposal)

    if not validation["valid"]:
        raise ValueError(
            "INVALID_AI_HYPOTHESIS:"
            + "|".join(validation["errors"])
        )

    def canonical_condition(
        condition: dict[str, Any],
    ) -> dict[str, Any]:

        out = {
            "primitive": condition["primitive"],
            "operator": condition["operator"],
        }

        if "window" in condition:
            out["window"] = condition["window"]

        if "value" in condition:
            out["value"] = _canonical_number(
                condition["value"]
            )

        if "parameter" in condition:
            out["parameter"] = condition["parameter"]

        return out

    def canonical_rule(
        rule: dict[str, Any],
    ) -> dict[str, Any]:

        return {
            "side": rule["side"],
            "logic": rule["logic"],
            "conditions": [
                canonical_condition(x)
                for x in rule["conditions"]
            ],
        }

    domain = {
        key: [
            _canonical_number(value)
            for value in values
        ]
        for key, values in sorted(
            proposal["finite_parameter_domain"].items()
        )
    }

    return {
        "grammar_schema": SCHEMA,
        "long_rule": canonical_rule(
            proposal["long_rule"]
        ),
        "short_rule": canonical_rule(
            proposal["short_rule"]
        ),
        "finite_parameter_domain": domain,
    }


def ai_hypothesis_fingerprint_v1(
    proposal: dict[str, Any],
) -> str:

    canonical = canonical_hypothesis(proposal)

    raw = json.dumps(
        canonical,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def self_test() -> None:

    valid = {
        "research_question":
            "Can efficiency-conditioned momentum improve 6H entries?",
        "hypothesis_name":
            "EFFICIENCY_MOMENTUM_V1",
        "rationale":
            "Require directional momentum only when price movement is efficient.",
        "novelty_basis":
            "Bounded conjunction of efficiency and momentum.",
        "expected_failure_modes": [
            "CHOP_REGIME",
            "LATE_TREND_ENTRY",
        ],
        "lookahead_risk":
            "Uses only closed-bar historical inputs.",
        "evidence_status":
            "UNTESTED",
        "decision":
            "HYPOTHESIS_ONLY",

        "finite_parameter_domain": {
            "eff_threshold": [0.5, 0.7, 0.9],
            "mom_threshold": [0.0, 0.01],
        },

        "long_rule": {
            "side": "LONG",
            "logic": "AND",
            "conditions": [
                {
                    "primitive": "EFFICIENCY",
                    "operator": "GT",
                    "window": 24,
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
                    "window": 24,
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

    result = validate_proposal(valid)

    assert result["valid"], result

    fp1 = ai_hypothesis_fingerprint_v1(valid)
    fp2 = ai_hypothesis_fingerprint_v1(valid)

    assert fp1 == fp2

    bad = json.loads(json.dumps(valid))
    bad["finite_parameter_domain"] = {}

    result_bad = validate_proposal(bad)

    assert not result_bad["valid"]
    assert (
        "FINITE_PARAMETER_DOMAIN_REQUIRED"
        in result_bad["errors"]
    )

    arbitrary = json.loads(json.dumps(valid))
    arbitrary["long_rule"]["conditions"][0][
        "primitive"
    ] = "AI_MAGIC_INDICATOR"

    result_arbitrary = validate_proposal(arbitrary)

    assert not result_arbitrary["valid"]

    print("SCHEMA=", SCHEMA)
    print("SELF_TEST=PASS")
    print("VALID_PROPOSAL=PASS")
    print("EMPTY_DOMAIN_REJECT=PASS")
    print("UNKNOWN_PRIMITIVE_REJECT=PASS")
    print("FINGERPRINT_DETERMINISTIC=PASS")
    print("ARBITRARY_CODE_EXECUTION=NO")
    print("RESEARCH_EXECUTION=NO")
    print("HARD_PASS_EXECUTION=NO")
    print("KNOWLEDGE_WRITE=NO")
    print("ORDER_WRITE=NO")
    print("POSITION_CHANGE=NO")
    print("FROZEN_CORE_MODIFIED=NO")
    print("SERVER_RELEASE=NO")


if __name__ == "__main__":
    self_test()
