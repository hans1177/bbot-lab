from __future__ import annotations

from pathlib import Path
from typing import Any

import v3_ai_entry_candidate_expander as expander
import v3_entry_hypothesis_wave_seal as seal


SCHEMA = "B_BOT_AI_ENTRY_WAVE_BUILDER_V1"


def build_manifest(
    proposal: dict[str, Any],
    timeframe: str,
    memory_reference: dict[str, Any],
) -> dict[str, Any]:

    candidates = expander.expand(
        proposal,
        timeframe,
    )

    provenance = {
        "schema": SCHEMA,
        "source": "AI_ENTRY_PROPOSAL",
        "timeframe": timeframe,
        "research_execution": False,
        "hard_pass_execution": False,
        "same_wave_mutation": False,
        "result_driven_mutation": False,
        "paid_ai_used": False,
        "cost_usd": 0.0,
    }

    return seal.make_manifest(
        candidates=candidates,
        memory_reference=memory_reference,
        provenance=provenance,
    )


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

    memory_reference = {
        "schema":
            "B_BOT_AI_MEMORY_REFERENCE_TEST_V1",
        "timeframe":
            "6H",
        "reference_only":
            True,
    }

    manifest = build_manifest(
        proposal,
        "6H",
        memory_reference,
    )

    candidates = (
        manifest.get("candidates")
        or []
    )

    assert len(candidates) == 2

    seal.validate_wave_candidates(
        candidates
    )

    assert manifest.get("wave_id")
    assert manifest.get("wave_hash")
    assert manifest.get(
        "memory_reference_hash"
    )

    print("SCHEMA=", SCHEMA)
    print("SELF_TEST=PASS")
    print(
        "CANDIDATE_COUNT=",
        len(candidates),
    )
    print(
        "WAVE_ID=",
        manifest["wave_id"],
    )
    print("SEAL_VALIDATION=PASS")
    print("IMMUTABLE_MANIFEST=PASS")
    print("FILE_WRITE=NO")
    print("AI_CALL=NO")
    print("RESEARCH_EXECUTION=NO")
    print("HARD_PASS_EXECUTION=NO")
    print("KNOWLEDGE_WRITE=NO")
    print("ORDER_WRITE=NO")


if __name__ == "__main__":
    self_test()
