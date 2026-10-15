from __future__ import annotations

import hashlib
import itertools
import json
from typing import Any

import v3_ai_entry_grammar as grammar
import v3_ai_entry_interpreter as interpreter


SCHEMA = "B_BOT_AI_ENTRY_CANDIDATE_EXPANDER_V1"

# Constitution/seal bound.
MAX_CANDIDATES_PER_PROPOSAL = 12


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        _canonical_json(value).encode("utf-8")
    ).hexdigest()


def concrete_parameter_sets(
    proposal: dict[str, Any],
) -> list[dict[str, Any]]:

    validation = grammar.validate_proposal(
        proposal
    )

    if not validation.get("valid"):
        raise RuntimeError(
            "AI_PROPOSAL_INVALID:"
            + "|".join(
                validation.get("errors") or []
            )
        )

    domain = proposal[
        "finite_parameter_domain"
    ]

    names = sorted(domain)

    values = [
        list(domain[name])
        for name in names
    ]

    total = 1

    for items in values:
        total *= len(items)

    if total <= 0:
        raise RuntimeError(
            "AI_PARAMETER_DOMAIN_EMPTY"
        )

    if total > MAX_CANDIDATES_PER_PROPOSAL:
        raise RuntimeError(
            "AI_PARAMETER_DOMAIN_TOO_LARGE:"
            + str(total)
        )

    rows = []

    for combination in itertools.product(
        *values
    ):
        rows.append(
            dict(zip(names, combination))
        )

    return rows


def executable_candidate(
    proposal: dict[str, Any],
    timeframe: str,
    parameter_values: dict[str, Any],
) -> dict[str, Any]:

    execution = (
        interpreter
        .validate_execution_contract(
            proposal,
            parameter_values,
        )
    )

    if not execution.get("valid"):
        raise RuntimeError(
            "AI_EXECUTION_CONTRACT_FAIL:"
            + "|".join(
                execution.get("errors") or []
            )
        )

    canonical = (
        grammar.canonical_hypothesis(
            proposal
        )
    )

    parent_fingerprint = (
        grammar.ai_hypothesis_fingerprint_v1(
            proposal
        )
    )

    concrete_identity = {
        "schema": SCHEMA,
        "timeframe": timeframe,
        "canonical_hypothesis": canonical,
        "parameter_values": parameter_values,
    }

    fingerprint = _sha256(
        concrete_identity
    )

    hypothesis_name = str(
        proposal.get("hypothesis_name")
        or "AI_ENTRY"
    ).upper()

    short_id = fingerprint[:12]

    return {
        "name":
            f"AI_{hypothesis_name}_{short_id}",
        "timeframe": timeframe,

        "grammar_schema":
            grammar.SCHEMA,

        "proposal_schema":
            grammar.SCHEMA,

        "research_question":
            proposal["research_question"],

        "hypothesis_name":
            proposal["hypothesis_name"],

        "rationale":
            proposal["rationale"],

        "novelty_basis":
            proposal["novelty_basis"],

        "expected_failure_modes":
            proposal["expected_failure_modes"],

        "lookahead_risk":
            proposal["lookahead_risk"],

        "evidence_status":
            proposal["evidence_status"],

        "decision":
            proposal["decision"],

        "finite_parameter_domain":
            proposal[
                "finite_parameter_domain"
            ],

        "parameter_values":
            parameter_values,

        "long_rule":
            proposal["long_rule"],

        "short_rule":
            proposal["short_rule"],

        # Unique identity for this concrete
        # executable parameter combination.
        "ai_hypothesis_fingerprint_v1":
            fingerprint,

        # Preserve parent proposal identity separately.
        "parent_ai_hypothesis_fingerprint_v1":
            parent_fingerprint,

        "bounded": True,
        "uses_future_data": False,
        "live_mutation": False,
        "same_wave_mutation": False,

        "execution_schema":
            interpreter.SCHEMA,

        "supported_primitives":
            sorted(
                execution.get(
                    "used_primitives"
                )
                or []
            ),
    }


def expand(
    proposal: dict[str, Any],
    timeframe: str,
) -> list[dict[str, Any]]:

    rows = []

    for parameters in (
        concrete_parameter_sets(
            proposal
        )
    ):
        rows.append(
            executable_candidate(
                proposal,
                timeframe,
                parameters,
            )
        )

    fingerprints = [
        row[
            "ai_hypothesis_fingerprint_v1"
        ]
        for row in rows
    ]

    if (
        len(fingerprints)
        != len(set(fingerprints))
    ):
        raise RuntimeError(
            "AI_CONCRETE_FINGERPRINT_DUPLICATE"
        )

    return rows


def self_test() -> None:

    proposal = {
        "research_question":
            "Can efficiency-conditioned momentum improve entries?",
        "hypothesis_name":
            "EFFICIENCY_MOMENTUM_V1",
        "rationale":
            "Require efficient directional movement.",
        "novelty_basis":
            "Bounded efficiency and momentum conjunction.",
        "expected_failure_modes": [
            "CHOP_REGIME"
        ],
        "lookahead_risk":
            "Uses closed-bar inputs only.",
        "evidence_status":
            "UNTESTED",
        "decision":
            "HYPOTHESIS_ONLY",

        "finite_parameter_domain": {
            "eff_threshold": [
                0.35,
                0.45,
            ],
            "mom_threshold": [
                0.0,
            ],
        },

        "long_rule": {
            "side": "LONG",
            "logic": "AND",
            "conditions": [
                {
                    "primitive":
                        "EFFICIENCY",
                    "operator": "GT",
                    "window": 20,
                    "parameter":
                        "eff_threshold",
                },
                {
                    "primitive":
                        "MOMENTUM",
                    "operator": "GT",
                    "window": 3,
                    "parameter":
                        "mom_threshold",
                },
            ],
        },

        "short_rule": {
            "side": "SHORT",
            "logic": "AND",
            "conditions": [
                {
                    "primitive":
                        "EFFICIENCY",
                    "operator": "GT",
                    "window": 20,
                    "parameter":
                        "eff_threshold",
                },
                {
                    "primitive":
                        "MOMENTUM",
                    "operator": "LT",
                    "window": 3,
                    "parameter":
                        "mom_threshold",
                },
            ],
        },
    }

    first = expand(
        proposal,
        "6H",
    )

    second = expand(
        proposal,
        "6H",
    )

    assert len(first) == 2
    assert first == second

    fingerprints = [
        x[
            "ai_hypothesis_fingerprint_v1"
        ]
        for x in first
    ]

    assert (
        len(fingerprints)
        == len(set(fingerprints))
    )

    assert all(
        x["timeframe"] == "6H"
        for x in first
    )

    assert all(
        x["bounded"] is True
        for x in first
    )

    print("SCHEMA=", SCHEMA)
    print("SELF_TEST=PASS")
    print("CANDIDATE_COUNT=", len(first))
    print("UNIQUE_FINGERPRINTS=PASS")
    print("DETERMINISTIC=PASS")
    print("BOUNDED_EXPANSION=PASS")
    print("SEAL_WRITE=NO")
    print("RESEARCH_EXECUTION=NO")
    print("HARD_PASS_EXECUTION=NO")
    print("KNOWLEDGE_WRITE=NO")
    print("ORDER_WRITE=NO")


if __name__ == "__main__":
    self_test()
