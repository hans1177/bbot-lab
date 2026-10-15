from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import v3_research_knowledge as knowledge


ROOT = Path(__file__).resolve().parents[1]
RESULTS_V3 = ROOT / "strategy_lab" / "results" / "v3"

PIPELINE_STAGE2 = (
    RESULTS_V3 / "historical_314_pipeline_stage2.json"
)

TF_ORDER = (
    "30M",
    "1H",
    "6H",
    "12H",
    "1D",
    "1W",
)

SCHEMA = "B_BOT_ENTRY_RESEARCH_MEMORY_V1"


# ------------------------------------------------------------
# Generic helpers
# ------------------------------------------------------------

def _finite(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(x):
        return None

    return x


def _bool(value: Any) -> bool:
    return value is True


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None

    try:
        return json.loads(
            path.read_text(encoding="utf-8-sig")
        )
    except Exception:
        return None


# ------------------------------------------------------------
# Evidence extraction
# ------------------------------------------------------------

def _evaluation(row: dict) -> dict:
    """
    Normalize the several evidence layouts already used by B-BOT.

    Read-only. No thresholds are invented here.
    """

    hard = _dict(
        _first(
            row.get("hard_pass_recheck"),
            row.get("evaluation"),
        )
    )

    final_gate = _dict(row.get("final_risk_gate"))
    full_gate = _dict(row.get("full_validation_gate"))

    evidence = _dict(row.get("evidence"))

    hard_wf = _finite(
        _first(
            hard.get("walk_forward_avg_net_pct"),
            evidence.get(
                "walk_forward_oos_expectancy_pct"
            ),
        )
    )

    hard_stress = _finite(
        _first(
            hard.get(
                "walk_forward_cost_stress_2x_avg_net_pct"
            ),
            evidence.get(
                "cost_stress_2x_expectancy_pct"
            ),
        )
    )

    development = _finite(
        hard.get("development_avg_net_pct")
    )

    holding = _finite(
        _first(
            hard.get("expected_holding_time_hours"),
            evidence.get("avg_holding_time_hours"),
        )
    )

    hard_pass = _first(
        hard.get("hard_pass"),
        hard.get("pass"),
    )

    final_pass = final_gate.get("pass")
    full_pass = full_gate.get("pass")

    return {
        "hard_pass": (
            bool(hard_pass)
            if hard_pass is not None
            else None
        ),
        "walk_forward_oos": hard_wf,
        "cost_stress_2x": hard_stress,
        "development": development,
        "holding_hours": holding,
        "final_risk_pass": (
            bool(final_pass)
            if final_pass is not None
            else None
        ),
        "full_validation_pass": (
            bool(full_pass)
            if full_pass is not None
            else None
        ),
    }


def _failure_reasons(row: dict) -> list[str]:
    out = []

    def add(value: Any) -> None:
        if isinstance(value, str):
            s = value.strip()
            if s:
                out.append(s)

        elif isinstance(value, list):
            for item in value:
                add(item)

    hard = _dict(
        _first(
            row.get("hard_pass_recheck"),
            row.get("evaluation"),
        )
    )

    entry = _dict(row.get("entry_risk_gate"))
    final = _dict(row.get("final_risk_gate"))
    full = _dict(row.get("full_validation_gate"))

    add(row.get("reason"))
    add(row.get("last_error"))

    add(hard.get("hard_reasons"))
    add(hard.get("reasons"))

    add(entry.get("absolute_fail_reasons"))
    add(entry.get("reason"))

    add(final.get("absolute_fail_reasons"))
    add(final.get("reason"))

    add(full.get("absolute_fail_reasons"))
    add(full.get("reason"))

    # Stable unique ordering.
    seen = set()
    clean = []

    for reason in out:
        if reason in seen:
            continue

        seen.add(reason)
        clean.append(reason)

    return clean


def _identity(record_key: str, row: dict) -> dict:
    strategy = _first(
        row.get("strategy_id"),
        row.get("strategy"),
        row.get("name"),
    )

    version = _first(
        row.get("exact_strategy_version"),
        row.get("strategy_version"),
    )

    tf = _upper(row.get("timeframe"))

    fingerprint = _first(
        row.get("semantic_fingerprint"),
        _dict(row.get("recipe")).get(
            "semantic_fingerprint"
        ),
    )

    source = _first(
        row.get("source"),
        row.get("research_source"),
        _dict(row.get("evidence_summary")).get(
            "source"
        ),
    )

    return {
        "record_key": record_key,
        "strategy": (
            str(strategy)
            if strategy is not None
            else None
        ),
        "exact_strategy_version": (
            str(version)
            if version is not None
            else None
        ),
        "timeframe": (
            tf if tf in TF_ORDER else None
        ),
        "semantic_fingerprint": (
            str(fingerprint)
            if fingerprint is not None
            else None
        ),
        "source": (
            str(source)
            if source is not None
            else None
        ),
    }


# ------------------------------------------------------------
# Raw memory readers
# ------------------------------------------------------------

def knowledge_records() -> dict[str, dict]:
    """
    Existing research knowledge is authoritative.
    This function never mutates it.
    """

    k, _ledger = knowledge.ensure()

    records = _dict(k).get("records")

    if not isinstance(records, dict):
        return {}

    return {
        str(key): value
        for key, value in records.items()
        if isinstance(value, dict)
    }


def pipeline_rows() -> list[dict]:
    raw = _read_json(PIPELINE_STAGE2)

    if isinstance(raw, list):
        rows = raw

    elif isinstance(raw, dict):
        rows = []

        for key in (
            "entries",
            "rows",
            "pipeline",
            "items",
            "strategies",
        ):
            value = raw.get(key)

            if isinstance(value, list):
                rows = value
                break
    else:
        rows = []

    return [
        row
        for row in rows
        if isinstance(row, dict)
    ]


# ------------------------------------------------------------
# Classification
# ------------------------------------------------------------

FAIL_STAGES = {
    "HARD_PASS_FAIL",
    "HARD_PASS_RECHECK_FAIL",
    "PERSISTENT_FAIL",
    "FINAL_RISK_REDEVELOPMENT_PENDING",
    "FULL_VALIDATION_REDEVELOPMENT_PENDING",
    "SEALED_FINAL_BLIND_FAIL_ANALYSIS_ONLY",
}

ROBUST_DOWNSTREAM_STAGES = {
    "INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING",
    "REENTRY_RESEARCH_PENDING",
    "FINAL_RISK_REVALIDATION_PENDING",
    "FULL_VALIDATION_PENDING",
    "SEALED_FINAL_BLIND_PENDING",
    "LOCAL_SHADOW_PENDING",
}


def _classify(row: dict) -> str:
    """
    Classification uses already-recorded pass/fail evidence.
    It does NOT create new qualification thresholds.
    """

    stage = _upper(row.get("pipeline_stage"))
    status = _upper(row.get("status"))

    ev = _evaluation(row)

    if (
        stage in FAIL_STAGES
        or status == "FAIL"
        or ev["hard_pass"] is False
    ):
        return "FAILURE"

    # Strong means the existing pipeline already recorded
    # positive hard-pass/WF/stress evidence.
    if (
        ev["hard_pass"] is True
        and ev["walk_forward_oos"] is not None
        and ev["walk_forward_oos"] > 0.0
        and ev["cost_stress_2x"] is not None
        and ev["cost_stress_2x"] > 0.0
    ):
        if stage in ROBUST_DOWNSTREAM_STAGES:
            return "STRONG"

        return "PASS"

    if status in {
        "PASS",
        "SOFT_PASS",
        "MEASURED",
    }:
        return "PASS"

    return "UNCLASSIFIED"


# ------------------------------------------------------------
# Merge without deleting history
# ------------------------------------------------------------

def _canonical_memory_rows() -> list[dict]:
    out = []

    # Primary research knowledge.
    for key, row in knowledge_records().items():
        ident = _identity(key, row)
        ev = _evaluation(row)

        out.append({
            **ident,
            "origin": "RESEARCH_KNOWLEDGE",
            "classification": _classify(row),
            "pipeline_stage": row.get(
                "pipeline_stage"
            ),
            "status": row.get("status"),
            "evaluation": ev,
            "failure_reasons": _failure_reasons(row),
            "result_json": row.get("result_json"),
            "family_file": _first(
                row.get("family_file"),
                row.get("canonical_source_file"),
            ),
        })

    # Unified/historical pipeline can contain richer downstream
    # evidence not represented in the original broad record.
    for idx, row in enumerate(pipeline_rows()):
        key = str(
            _first(
                row.get("source_record_key"),
                row.get("canonical_identity_key"),
                f"pipeline:{idx}",
            )
        )

        ident = _identity(key, row)
        ev = _evaluation(row)

        out.append({
            **ident,
            "origin": "PIPELINE_STAGE2",
            "classification": _classify(row),
            "pipeline_stage": row.get(
                "pipeline_stage"
            ),
            "status": row.get("status"),
            "evaluation": ev,
            "failure_reasons": _failure_reasons(row),
            "result_json": _first(
                row.get("new_result_json"),
                row.get("result_json"),
                row.get("previous_result_json"),
            ),
            "family_file": _first(
                row.get("canonical_source_file"),
                row.get("family_file"),
            ),
        })

    return out


# ------------------------------------------------------------
# Aggregation
# ------------------------------------------------------------

def build_entry_research_memory() -> dict:
    """
    Build a disposable, read-only interpretation of all accumulated
    research evidence.

    The returned object is derived memory, not a new authority.
    """

    rows = _canonical_memory_rows()

    class_counts = Counter(
        row["classification"]
        for row in rows
    )

    timeframe_counts = {
        tf: Counter()
        for tf in TF_ORDER
    }

    failure_reasons = Counter()
    known_fingerprints = set()

    strong = []
    passed = []
    failed = []

    strategy_summary = defaultdict(
        lambda: {
            "records": 0,
            "strong": 0,
            "pass": 0,
            "failure": 0,
            "timeframes": set(),
            "best_wf": None,
            "best_stress": None,
        }
    )

    for row in rows:
        tf = row.get("timeframe")
        cls = row["classification"]

        if tf in timeframe_counts:
            timeframe_counts[tf][cls] += 1

        fp = row.get("semantic_fingerprint")

        if fp:
            known_fingerprints.add(fp)

        for reason in row["failure_reasons"]:
            failure_reasons[reason] += 1

        if cls == "STRONG":
            strong.append(row)

        elif cls == "PASS":
            passed.append(row)

        elif cls == "FAILURE":
            failed.append(row)

        strategy = row.get("strategy")

        if strategy:
            s = strategy_summary[strategy]

            s["records"] += 1

            if cls == "STRONG":
                s["strong"] += 1

            elif cls == "PASS":
                s["pass"] += 1

            elif cls == "FAILURE":
                s["failure"] += 1

            if tf:
                s["timeframes"].add(tf)

            wf = row["evaluation"].get(
                "walk_forward_oos"
            )

            stress = row["evaluation"].get(
                "cost_stress_2x"
            )

            if wf is not None:
                if (
                    s["best_wf"] is None
                    or wf > s["best_wf"]
                ):
                    s["best_wf"] = wf

            if stress is not None:
                if (
                    s["best_stress"] is None
                    or stress > s["best_stress"]
                ):
                    s["best_stress"] = stress

    # Rank only for research-reference priority.
    # This NEVER promotes a strategy or changes thresholds.
    def strong_rank(row: dict):
        ev = row["evaluation"]

        wf = ev.get("walk_forward_oos")
        stress = ev.get("cost_stress_2x")
        dev = ev.get("development")
        holding = ev.get("holding_hours")

        return (
            stress if stress is not None else -1e100,
            wf if wf is not None else -1e100,
            dev if dev is not None else -1e100,
            -(holding if holding is not None else 1e100),
        )

    strong.sort(
        key=strong_rank,
        reverse=True,
    )

    passed.sort(
        key=strong_rank,
        reverse=True,
    )

    # Convert sets to JSON-safe values.
    summary_clean = {}

    for strategy, row in strategy_summary.items():
        summary_clean[strategy] = {
            **row,
            "timeframes": sorted(
                row["timeframes"],
                key=lambda x: (
                    TF_ORDER.index(x)
                    if x in TF_ORDER
                    else 999
                ),
            ),
        }

    return {
        "schema": SCHEMA,

        "authority": (
            "DERIVED_READ_ONLY_RESEARCH_MEMORY"
        ),

        "source_authority": [
            "v3_research_knowledge",
            str(
                PIPELINE_STAGE2.relative_to(ROOT)
            ).replace("\\", "/"),
        ],

        "record_count": len(rows),

        "classification_counts": dict(
            class_counts
        ),

        "timeframes": {
            tf: dict(timeframe_counts[tf])
            for tf in TF_ORDER
        },

        "strong_evidence": strong,

        "pass_evidence": passed,

        "failure_evidence": failed,

        "failure_patterns": [
            {
                "reason": reason,
                "count": count,
            }
            for reason, count
            in failure_reasons.most_common()
        ],

        "known_semantic_fingerprints": sorted(
            known_fingerprints
        ),

        "strategy_summary": summary_clean,

        "research_rules": {
            "historical_results_preserved": True,
            "pass_results_preserved": True,
            "fail_results_preserved": True,
            "failure_reason_reused_as_evidence": True,

            "same_failed_candidate_retry_authorized":
                False,

            "memory_can_promote_strategy":
                False,

            "memory_can_lower_gate_threshold":
                False,

            "memory_can_mutate_same_wave":
                False,

            "next_wave_can_use_prior_evidence":
                True,
        },
    }


def _reference_identity(row: dict) -> tuple:
    """
    Logical evidence identity for hypothesis-reference use.

    Storage origin, result_json path, pipeline stage and version aliases
    are deliberately excluded so the same measured strategy evidence
    is not presented multiple times merely because it was persisted in
    more than one research layer.
    """
    ev = _dict(row.get("evaluation"))

    def norm_num(value):
        x = _finite(value)
        return round(x, 10) if x is not None else None

    return (
        str(row.get("strategy") or "").upper(),
        str(row.get("timeframe") or "").upper(),
        str(row.get("classification") or "").upper(),
        norm_num(ev.get("walk_forward_oos")),
        norm_num(ev.get("cost_stress_2x")),
        norm_num(ev.get("development")),
        tuple(sorted(str(x) for x in (row.get("failure_reasons") or []))),
    )


def _usable_reference(row: dict) -> bool:
    """
    Hypothesis references must have an identifiable strategy.

    Anonymous raw evidence remains preserved in the authoritative
    Research Memory; it is only excluded from parent/critic context.
    """
    return bool(
        isinstance(row, dict)
        and str(row.get("strategy") or "").strip()
        and str(row.get("timeframe") or "").upper() in TF_ORDER
    )


def _reference_quality(row: dict) -> tuple:
    """
    Preference only for choosing between duplicate representations.
    No research qualification or promotion authority.
    """
    ev = _dict(row.get("evaluation"))

    origin_score = (
        2
        if str(row.get("origin") or "").upper() == "PIPELINE_STAGE2"
        else 1
    )

    identity_score = sum(
        1
        for value in (
            row.get("exact_strategy_version"),
            row.get("semantic_fingerprint"),
            row.get("result_json"),
            row.get("family_file"),
            row.get("pipeline_stage"),
        )
        if value not in (None, "")
    )

    evidence_score = sum(
        1
        for key in (
            "hard_pass",
            "walk_forward_oos",
            "cost_stress_2x",
            "development",
            "holding_hours",
            "final_risk_pass",
            "full_validation_pass",
        )
        if ev.get(key) is not None
    )

    return (
        origin_score,
        evidence_score,
        identity_score,
    )


def _dedup_references(rows: list[dict]) -> list[dict]:
    """
    Produce a disposable hypothesis-reference view.

    Raw records are neither deleted nor modified.
    """
    best = {}
    order = []

    for row in rows:
        if not _usable_reference(row):
            continue

        key = _reference_identity(row)

        if key not in best:
            best[key] = row
            order.append(key)
            continue

        if _reference_quality(row) > _reference_quality(best[key]):
            best[key] = row

    return [
        best[key]
        for key in order
    ]


def hypothesis_context(
    timeframe: str | None = None,
    *,
    strong_limit: int = 25,
    failure_limit: int = 25,
) -> dict:
    """
    Compact context intended for a future hypothesis generator.

    No candidate is generated here.
    """

    memory = build_entry_research_memory()

    tf = _upper(timeframe)

    strong = _dedup_references(
        memory["strong_evidence"]
    )
    failed = _dedup_references(
        memory["failure_evidence"]
    )

    if tf in TF_ORDER:
        strong = [
            row for row in strong
            if row.get("timeframe") == tf
        ]

        failed = [
            row for row in failed
            if row.get("timeframe") == tf
        ]

    return {
        "schema": (
            "B_BOT_ENTRY_HYPOTHESIS_CONTEXT_V1"
        ),

        "timeframe": (
            tf if tf in TF_ORDER else None
        ),

        "strong_references": strong[
            :max(0, int(strong_limit))
        ],

        "failure_references": failed[
            :max(0, int(failure_limit))
        ],

        "failure_patterns": memory[
            "failure_patterns"
        ][:max(0, int(failure_limit))],

        "known_semantic_fingerprints": memory[
            "known_semantic_fingerprints"
        ],

        "rules": memory["research_rules"],
    }


def status() -> dict:
    memory = build_entry_research_memory()

    return {
        "schema": SCHEMA,
        "record_count": memory["record_count"],
        "classification_counts":
            memory["classification_counts"],
        "fingerprint_count": len(
            memory["known_semantic_fingerprints"]
        ),
        "top_failure_patterns":
            memory["failure_patterns"][:10],
        "strong_reference_count": len(
            memory["strong_evidence"]
        ),
        "pass_reference_count": len(
            memory["pass_evidence"]
        ),
        "failure_reference_count": len(
            memory["failure_evidence"]
        ),
    }


if __name__ == "__main__":
    print(
        json.dumps(
            status(),
            ensure_ascii=False,
            indent=2,
        )
    )
