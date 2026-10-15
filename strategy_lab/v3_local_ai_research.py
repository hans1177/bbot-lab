from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Any

import v3_ai_research_director as director
import v3_ai_entry_grammar as entry_grammar


SCHEMA = "B_BOT_LOCAL_AI_RESEARCH_V1"

OLLAMA_HOST = "http://127.0.0.1:11434"
OLLAMA_GENERATE = OLLAMA_HOST + "/api/generate"

MODEL = "qwen3:1.7b"

CONNECT_TIMEOUT_SECONDS = 5
REQUEST_TIMEOUT_SECONDS = 120

# Keep prompts compact.
MAX_STRONG_REFERENCES = 3
MAX_FAILURE_REFERENCES = 3
MAX_KNOWN_FP = 23


SYSTEM_CONTRACT = """
You are a local B-BOT entry-strategy research assistant.

PRIMARY GOAL:
Discover novel ENTRY hypotheses that have a realistic chance of
passing the EXISTING B-BOT Hard Pass, Walk-Forward/OOS and Cost Stress.

The goal is NOT to make a candidate appear to pass.
The existing validation thresholds are immutable.

STRICT RULES:
- ENTRY research only.
- No exit optimization to rescue an entry.
- No threshold relaxation.
- No future data or lookahead.
- No search-until-winner.
- No same-wave result-driven mutation.
- Known failed structures are evidence, not targets for repetition.
- External popularity or claims are not profitability evidence.
- Never promote a strategy to live trading.
- B-BOT validation is the only evidence authority.
- Return only the requested JSON object.
""".strip()


def _compact_reference(
    row: dict[str, Any],
) -> dict[str, Any]:

    keys = (
        "strategy",
        "timeframe",
        "classification",
        "walk_forward_return_pct",
        "cost_stress_return_pct",
        "development_return_pct",
        "failure_reasons",
    )

    return {
        k: row.get(k)
        for k in keys
        if k in row
    }


def build_prompt(
    timeframe: str,
) -> str:

    return f"""
Create ONE B-BOT ENTRY HYPOTHESIS for timeframe {timeframe}.

Return JSON ONLY.

Use EXACTLY this structure:

{{
  "research_question": "short",
  "hypothesis_name": "SHORT_NAME",
  "rationale": "short",
  "novelty_basis": "short",
  "expected_failure_modes": ["A", "B"],
  "lookahead_risk": "closed-bar only",
  "evidence_status": "UNTESTED",
  "decision": "HYPOTHESIS_ONLY",

  "finite_parameter_domain": {{
    "eff_threshold": [0.35, 0.45]
  }},

  "long_rule": {{
    "side": "LONG",
    "logic": "AND",
    "conditions": [
      {{
        "primitive": "EFFICIENCY",
        "operator": "GT",
        "window": 20,
        "parameter": "eff_threshold"
      }}
    ]
  }},

  "short_rule": {{
    "side": "SHORT",
    "logic": "AND",
    "conditions": [
      {{
        "primitive": "EFFICIENCY",
        "operator": "GT",
        "window": 20,
        "parameter": "eff_threshold"
      }}
    ]
  }}
}}

Rules:
- long_rule MUST be an object.
- short_rule MUST be an object.
- Never use a nested "rule" field.
- Never use "parameters" fields.
- Each condition uses exactly one of value or parameter.
- Every parameter must exist in finite_parameter_domain.
- evidence_status MUST be "UNTESTED".
- decision MUST be "HYPOTHESIS_ONLY".
- Use only closed-bar inputs.
- Keep all text short.
- Do not output markdown.
- Do not output extra keys.
"""


def ollama_generate(
    prompt: str,
) -> dict[str, Any]:

    body = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "keep_alive": 0,
        "think": False,

        # Ollama should return JSON, not chat prose.
        "format": ENTRY_GRAMMAR_JSON_SCHEMA,

        "options": {
            "temperature": 0.1,
            "num_ctx": 2048,
            "num_predict": 700,
        },
    }

    data = json.dumps(
        body,
        ensure_ascii=False,
    ).encode("utf-8")

    req = urllib.request.Request(
        OLLAMA_GENERATE,
        data=data,
        headers={
            "Content-Type":
                "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(
        req,
        timeout=REQUEST_TIMEOUT_SECONDS,
    ) as response:

        raw = response.read().decode(
            "utf-8"
        )

    result = json.loads(raw)

    text = str(
        result.get("response", "")
    ).strip()

    if not text:
        raise RuntimeError(
            "EMPTY_LOCAL_AI_RESPONSE"
        )

    try:
        hypothesis = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "LOCAL_AI_RESPONSE_NOT_JSON"
        ) from exc

    return {
        "model":
            result.get("model", MODEL),

        "done":
            bool(result.get("done")),

        "hypothesis":
            hypothesis,
    }


def validate_hypothesis(
    row: dict[str, Any],
) -> list[str]:

    errors = []

    required = (
        "research_question",
        "hypothesis_name",
        "rationale",
        "novelty_basis",
        "long_rule",
        "short_rule",
        "finite_parameter_domain",
        "expected_failure_modes",
        "lookahead_risk",
        "evidence_status",
        "decision",
    )

    for key in required:
        if key not in row:
            errors.append(
                "MISSING_" + key.upper()
            )

    if row.get(
        "evidence_status"
    ) != "UNTESTED":
        errors.append(
            "EVIDENCE_STATUS_MUST_BE_UNTESTED"
        )

    if row.get(
        "decision"
    ) != "HYPOTHESIS_ONLY":
        errors.append(
            "DECISION_MUST_BE_HYPOTHESIS_ONLY"
        )

    domain = row.get(
        "finite_parameter_domain"
    )

    if not isinstance(domain, dict):
        errors.append(
            "FINITE_PARAMETER_DOMAIN_REQUIRED"
        )
    else:
        for key, values in domain.items():

            if not isinstance(values, list):
                errors.append(
                    "NONFINITE_DOMAIN_" +
                    str(key).upper()
                )
                continue

            if not values:
                errors.append(
                    "EMPTY_DOMAIN_" +
                    str(key).upper()
                )

            if len(values) > 12:
                errors.append(
                    "DOMAIN_TOO_LARGE_" +
                    str(key).upper()
                )

    return errors


def research_once(
    timeframe: str,
) -> dict[str, Any]:

    tf = timeframe.upper()

    if tf not in director.TF_ORDER:
        raise ValueError(
            "UNSUPPORTED_TIMEFRAME"
        )

    prompt = build_prompt(tf)

    result = ollama_generate(
        prompt
    )

    hypothesis = result[
        "hypothesis"
    ]

    # Deterministic normalization of one known
    # local-model wrapper shape. This does not
    # weaken grammar validation.
    if isinstance(hypothesis, dict):
        for side_key in ("long_rule", "short_rule"):
            rule_obj = hypothesis.get(side_key)

            if (
                isinstance(rule_obj, dict)
                and isinstance(rule_obj.get("rule"), dict)
                and "rule" in rule_obj
            ):
                wrapper_keys = set(rule_obj.keys()) - {"rule"}
                allowed_wrapper_keys = {
                    "reason",
                    "description",
                    "parameters",
                    "parameter",
                }

                if wrapper_keys.issubset(
                    allowed_wrapper_keys
                ):
                    hypothesis[side_key] = rule_obj["rule"]

    errors = validate_hypothesis(
        hypothesis
    )

    grammar_result = (
        entry_grammar.validate_proposal(
            hypothesis
        )
    )

    for grammar_error in grammar_result["errors"]:
        tagged = (
            "ENTRY_GRAMMAR:"
            + grammar_error
        )

        if tagged not in errors:
            errors.append(tagged)

    ai_hypothesis_fingerprint = None

    if not errors:
        ai_hypothesis_fingerprint = (
            entry_grammar.ai_hypothesis_fingerprint_v1(
                hypothesis
            )
        )

    return {
        "schema": SCHEMA,
        "timeframe": tf,
        "provider": "LOCAL_OLLAMA",
        "model": result["model"],

        "cost_usd": 0.0,

        "primary_objective":
            director.PRIMARY_OBJECTIVE,

        "hypothesis":
            hypothesis,

        "validation_errors":
            errors,

        "proposal_valid":
            not errors,

        "ai_hypothesis_fingerprint_v1":
            ai_hypothesis_fingerprint,

        "entry_grammar_schema":
            entry_grammar.SCHEMA,

        "research_execution":
            False,

        "hard_pass_execution":
            False,

        "knowledge_write":
            False,

        "automatic_promotion":
            False,
    }


if __name__ == "__main__":

    # First smoke test only.
    tf = "6H"

    print("SCHEMA=", SCHEMA)
    print("PROVIDER=LOCAL_OLLAMA")
    print("MODEL=", MODEL)
    print("TIMEFRAME=", tf)
    print("MAX_AI_COST_USD=0.0")
    print("PAID_AI=FORBIDDEN")
    print("REQUEST=START")

    try:
        result = research_once(tf)

        print("REQUEST=PASS")

        print(
            "PROPOSAL_VALID=",
            result["proposal_valid"],
        )

        print(
            "VALIDATION_ERRORS=",
            result["validation_errors"],
        )

        print("\n=== AI HYPOTHESIS ===")

        print(
            json.dumps(
                result["hypothesis"],
                ensure_ascii=False,
                indent=2,
            )
        )

    except Exception as exc:

        print("REQUEST=FAIL")

        print(
            "ERROR_TYPE=",
            type(exc).__name__,
        )

        print(
            "ERROR=",
            str(exc),
        )

    print("\nAI_CHAT_UI=NO")
    print("AI_THINKING_OUTPUT_TO_USER=NO")
    print("RESEARCH_EXECUTION=NO")
    print("HARD_PASS_EXECUTION=NO")
    print("KNOWLEDGE_WRITE=NO")
    print("ORDER_WRITE=NO")
    print("POSITION_CHANGE=NO")
    print("FROZEN_CORE_MODIFIED=NO")
    print("SERVER_RELEASE=NO")
