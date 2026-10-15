from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import v3_entry_research_memory as memory


SCHEMA = "B_BOT_AI_RESEARCH_DIRECTOR_V1"

TF_ORDER = (
    "30M",
    "1H",
    "6H",
    "12H",
    "1D",
    "1W",
)


# ============================================================
# ZERO-COST CONTRACT
# ============================================================

MAX_AI_COST_USD = 0.0
ALLOW_PAID_AI = False
PAID_FALLBACK = False
AUTO_BILLING_ALLOWED = False
CREDIT_PURCHASE_ALLOWED = False

# Provider names are capability slots.
# Availability is NOT assumed merely from the provider name.
PROVIDER_ORDER = (
    "FREE_CLOUD_PRIMARY",
    "FREE_CLOUD_SECONDARY",
    "FREE_CLOUD_TERTIARY",
    "LOCAL_MODEL",
)


@dataclass(frozen=True)
class ProviderState:
    name: str
    enabled: bool
    explicitly_free: bool
    cost_risk: bool
    quota_available: bool | None
    status: str


def default_provider_states() -> tuple[ProviderState, ...]:
    """
    No external provider is enabled until its zero-cost status
    and credentials are configured explicitly.

    Local model is the safe default research assistant.
    """
    return (
        ProviderState(
            name="FREE_CLOUD_PRIMARY",
            enabled=False,
            explicitly_free=False,
            cost_risk=False,
            quota_available=None,
            status="NOT_CONFIGURED",
        ),
        ProviderState(
            name="FREE_CLOUD_SECONDARY",
            enabled=False,
            explicitly_free=False,
            cost_risk=False,
            quota_available=None,
            status="NOT_CONFIGURED",
        ),
        ProviderState(
            name="FREE_CLOUD_TERTIARY",
            enabled=False,
            explicitly_free=False,
            cost_risk=False,
            quota_available=None,
            status="NOT_CONFIGURED",
        ),
        ProviderState(
            name="LOCAL_MODEL",
            enabled=True,
            explicitly_free=True,
            cost_risk=False,
            quota_available=True,
            status="AVAILABLE_IF_LOCAL_RUNTIME_EXISTS",
        ),
    )


def provider_allowed(
    state: ProviderState,
) -> bool:

    if MAX_AI_COST_USD != 0.0:
        raise RuntimeError(
            "ZERO_COST_CONTRACT_BROKEN"
        )

    if ALLOW_PAID_AI:
        raise RuntimeError(
            "PAID_AI_MUST_REMAIN_DISABLED"
        )

    if PAID_FALLBACK:
        raise RuntimeError(
            "PAID_FALLBACK_MUST_REMAIN_DISABLED"
        )

    if not state.enabled:
        return False

    if not state.explicitly_free:
        return False

    if state.cost_risk:
        return False

    if state.quota_available is False:
        return False

    return True


def provider_route(
    states: tuple[ProviderState, ...] | None = None,
) -> list[str]:

    states = (
        states
        if states is not None
        else default_provider_states()
    )

    by_name = {
        x.name: x
        for x in states
    }

    out = []

    for name in PROVIDER_ORDER:
        state = by_name.get(name)

        if state is None:
            continue

        if provider_allowed(state):
            out.append(name)

    return out


# ============================================================
# RESEARCH AUTHORITY CONTRACT
# ============================================================

PRIMARY_OBJECTIVE = (
    "Discover novel entry hypotheses with a realistic chance of "
    "passing the existing B-BOT Hard Pass, Walk-Forward/OOS and "
    "Cost Stress requirements without changing validation thresholds."
)

NON_NEGOTIABLE_RULES = (
    "VALIDATION_THRESHOLDS_IMMUTABLE",
    "FAILURE_IS_VALID_EVIDENCE",
    "NO_SEARCH_UNTIL_WINNER",
    "NO_SAME_WAVE_RESULT_DRIVEN_MUTATION",
    "NO_KNOWN_FAILED_FINGERPRINT_REPEAT",
    "NO_LOOKAHEAD",
    "NO_FUTURE_DATA",
    "NO_EXIT_RESCUE_OF_UNQUALIFIED_ENTRY",
    "ENTRY_EXIT_PARAMETER_SEARCH_SEPARATED",
    "NO_AI_AUTOMATIC_PROMOTION",
    "NO_EXTERNAL_PERFORMANCE_CLAIM_AS_EVIDENCE",
    "NO_UNTRUSTED_EXTERNAL_CODE_EXECUTION",
    "PAID_AI_FORBIDDEN",
)


# ============================================================
# AI TEAM
# ============================================================

AI_ROLES = {
    "RESEARCH_DIRECTOR": (
        "Choose bounded research questions from existing evidence "
        "and coordinate independent research roles."
    ),

    "MEMORY_ANALYST": (
        "Analyze strong, failed and intermediate historical evidence "
        "without changing raw evidence."
    ),

    "ENTRY_GENERATOR": (
        "Propose novel ENTRY hypotheses only."
    ),

    "MARKET_INTERPRETER": (
        "Propose bounded OHLCV interpretations and regime hypotheses "
        "without future information."
    ),

    "EXTERNAL_SCOUT": (
        "Extract research ideas from public sources. External claims "
        "are hypothesis sources only."
    ),

    "RED_TEAM": (
        "Attack hypotheses for lookahead, overfit, weak trade count, "
        "cost fragility, concentration and repeated failure structure."
    ),

    "NOVELTY_CRITIC": (
        "Compare proposed ideas with known strategies and semantic "
        "fingerprints."
    ),

    "EXIT_RESEARCHER": (
        "Research exits only after a qualifying entry reaches the "
        "existing downstream exit/trailing stage."
    ),

    "JOINT_SYNTHESIZER": (
        "Synthesize independent research opinions into a bounded "
        "hypothesis proposal without granting PASS authority."
    ),
}


# ============================================================
# ENTRY / EXIT COMMUNICATION
# ============================================================

ENTRY_EXIT_COMMUNICATION = {
    "direct_joint_parameter_optimization":
        False,

    "shared_raw_evidence":
        True,

    "shared_research_memory":
        True,

    "entry_may_inform_exit":
        True,

    "exit_may_inform_future_entry_wave":
        True,

    "exit_feedback_may_mutate_current_entry_wave":
        False,

    "entry_required_before_exit_research":
        (
            "EXISTING_PIPELINE_QUALIFICATION"
        ),
}


# ============================================================
# HOMEWORK
# ============================================================

ENTRY_HOMEWORK = (
    "Analyze recent failure patterns.",
    "Analyze structural commonalities among robust references.",
    "Identify under-researched entry structures.",
    "Propose structurally novel entry hypotheses.",
    "Propose regime-specific entry hypotheses.",
    "Generate counterexamples against promising hypotheses.",
    "Compare against known semantic fingerprints.",
    "Analyze public-source ideas when available.",
    "Propose bounded OHLCV interpretations.",
    "Submit candidates for the NEXT bounded wave only.",
)


JOINT_HOMEWORK = (
    "State one falsifiable research question.",
    "Collect supporting historical evidence.",
    "Collect contradictory historical evidence.",
    "Produce at least one independent alternative explanation.",
    "Describe expected failure regimes.",
    "Describe novelty relative to existing strategies.",
    "Define finite parameter domain before testing.",
    "Submit to Red Team before wave freeze.",
)


# ============================================================
# RESEARCH KPI
# ============================================================

RESEARCH_KPI = (
    "HARD_PASS_SURVIVAL_RATE",
    "WF_OOS_SURVIVAL_RATE",
    "COST_STRESS_SURVIVAL_RATE",
    "DUPLICATE_HYPOTHESIS_RATE",
    "REPEATED_FAILURE_PATTERN_RATE",
    "STRUCTURAL_NOVELTY",
    "EVIDENCE_ROBUSTNESS",
)

KPI_MAY_CHANGE_THRESHOLDS = False


def homework(
    timeframe: str,
) -> dict[str, Any]:

    tf = str(timeframe).upper()

    if tf not in TF_ORDER:
        raise ValueError(
            "UNSUPPORTED_TIMEFRAME"
        )

    ctx = memory.hypothesis_context(
        tf,
        strong_limit=8,
        failure_limit=8,
    )

    return {
        "schema": SCHEMA,
        "timeframe": tf,

        "primary_objective":
            PRIMARY_OBJECTIVE,

        "roles":
            AI_ROLES,

        "entry_homework":
            list(ENTRY_HOMEWORK),

        "joint_homework":
            list(JOINT_HOMEWORK),

        "non_negotiable_rules":
            list(NON_NEGOTIABLE_RULES),

        "research_kpi":
            list(RESEARCH_KPI),

        "kpi_may_change_thresholds":
            KPI_MAY_CHANGE_THRESHOLDS,

        "memory_context": {
            "strong_references":
                ctx["strong_references"],

            "failure_references":
                ctx["failure_references"],

            "known_semantic_fingerprints":
                ctx[
                    "known_semantic_fingerprints"
                ],
        },

        "entry_exit_communication":
            ENTRY_EXIT_COMMUNICATION,

        "decision":
            "RESEARCH_HOMEWORK_ONLY",

        "automatic_promotion":
            False,
    }


def status() -> dict[str, Any]:

    states = default_provider_states()

    return {
        "schema": SCHEMA,

        "cost_policy": {
            "max_ai_cost_usd":
                MAX_AI_COST_USD,

            "allow_paid_ai":
                ALLOW_PAID_AI,

            "paid_fallback":
                PAID_FALLBACK,

            "auto_billing_allowed":
                AUTO_BILLING_ALLOWED,

            "credit_purchase_allowed":
                CREDIT_PURCHASE_ALLOWED,
        },

        "provider_states": [
            asdict(x)
            for x in states
        ],

        "provider_route":
            provider_route(states),

        "primary_objective":
            PRIMARY_OBJECTIVE,

        "non_negotiable_rules":
            list(NON_NEGOTIABLE_RULES),

        "entry_exit_communication":
            ENTRY_EXIT_COMMUNICATION,

        "research_execution":
            False,

        "ai_external_call":
            False,

        "automatic_promotion":
            False,
    }


if __name__ == "__main__":
    s = status()

    print("SCHEMA=", s["schema"])

    print(
        "MAX_AI_COST_USD=",
        s["cost_policy"][
            "max_ai_cost_usd"
        ],
    )

    print(
        "ALLOW_PAID_AI=",
        s["cost_policy"][
            "allow_paid_ai"
        ],
    )

    print(
        "PAID_FALLBACK=",
        s["cost_policy"][
            "paid_fallback"
        ],
    )

    print(
        "PROVIDER_ROUTE=",
        s["provider_route"],
    )

    print(
        "PRIMARY_OBJECTIVE=",
        s["primary_objective"],
    )

    for tf in TF_ORDER:
        h = homework(tf)

        print(
            "\nTF=",
            tf,
            "STRONG=",
            len(
                h["memory_context"][
                    "strong_references"
                ]
            ),
            "FAIL=",
            len(
                h["memory_context"][
                    "failure_references"
                ]
            ),
            "KNOWN_FP=",
            len(
                h["memory_context"][
                    "known_semantic_fingerprints"
                ]
            ),
        )

    print("\nAI_EXTERNAL_CALL=NO")
    print("RESEARCH_EXECUTION=NO")
    print("KNOWLEDGE_WRITE=NO")
    print("ORDER_WRITE=NO")
    print("POSITION_CHANGE=NO")
    print("FROZEN_CORE_MODIFIED=NO")
    print("SERVER_RELEASE=NO")
