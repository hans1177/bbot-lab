from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "B_BOT_ENTRY_HYPOTHESIS_WAVE_SEAL_V1"

RESULT_ROOT = (
    Path(__file__).resolve().parent
    / "results"
    / "v3"
    / "entry_hypothesis_waves"
)

MAX_CANDIDATES_PER_TF = 12
MAX_STRUCTURAL_GROUP_COUNT = 4
MAX_PER_STRUCTURAL_GROUP = 3

TRADING_TIMEFRAMES = (
    "30M",
    "1H",
    "6H",
    "12H",
    "1D",
    "1W",
)


def canonical_json(
    value: Any,
) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_json(
    value: Any,
) -> str:
    return hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()


def structural_group(
    candidate: dict[str, Any],
) -> tuple[str, str]:

    mode = str(
        candidate.get("mode", "")
    ).strip().upper()

    confirm_mode = str(
        candidate.get("confirm_mode", "")
    ).strip().upper()

    if not mode:
        mode = "UNKNOWN"

    if not confirm_mode:
        confirm_mode = "NONE"

    return mode, confirm_mode


def candidate_identity(
    candidate: dict[str, Any],
) -> str:

    for key in (
        "ai_hypothesis_fingerprint_v1",
        "semantic_fingerprint",
        "fingerprint",
    ):
        value = candidate.get(key)

        if isinstance(value, str) and value.strip():
            return value.strip()

    # Fail-closed deterministic fallback.
    # This is only manifest identity, not historical
    # semantic_recipe_fingerprint replacement.
    return sha256_json({
        "timeframe":
            candidate.get("timeframe"),
        "mode":
            candidate.get("mode"),
        "confirm_mode":
            candidate.get("confirm_mode"),
        "window":
            candidate.get("window"),
        "threshold":
            candidate.get("threshold"),
        "finite_parameter_domain":
            candidate.get(
                "finite_parameter_domain"
            ),
        "long_rule":
            candidate.get("long_rule"),
        "short_rule":
            candidate.get("short_rule"),
    })


def validate_wave_candidates(
    candidates: list[dict[str, Any]],
) -> list[str]:

    errors: list[str] = []

    by_tf: dict[str, list[dict[str, Any]]] = {
        tf: []
        for tf in TRADING_TIMEFRAMES
    }

    identities_by_tf: dict[str, set[str]] = {
        tf: set()
        for tf in TRADING_TIMEFRAMES
    }

    for index, candidate in enumerate(candidates):

        if not isinstance(candidate, dict):
            errors.append(
                f"CANDIDATE_{index}:NOT_OBJECT"
            )
            continue

        tf = str(
            candidate.get("timeframe", "")
        ).upper()

        if tf not in TRADING_TIMEFRAMES:
            errors.append(
                f"CANDIDATE_{index}:INVALID_TIMEFRAME"
            )
            continue

        identity = candidate_identity(
            candidate
        )

        if identity in identities_by_tf[tf]:
            errors.append(
                f"{tf}:DUPLICATE_IDENTITY:{identity}"
            )

        identities_by_tf[tf].add(
            identity
        )

        by_tf[tf].append(
            candidate
        )

    for tf, rows in by_tf.items():

        if len(rows) > MAX_CANDIDATES_PER_TF:
            errors.append(
                f"{tf}:TOO_MANY_CANDIDATES:"
                f"{len(rows)}"
            )

        group_counts: dict[
            tuple[str, str],
            int
        ] = {}

        for row in rows:
            group = structural_group(row)

            group_counts[group] = (
                group_counts.get(group, 0)
                + 1
            )

        if len(group_counts) > MAX_STRUCTURAL_GROUP_COUNT:
            errors.append(
                f"{tf}:TOO_MANY_STRUCTURAL_GROUPS:"
                f"{len(group_counts)}"
            )

        for group, count in group_counts.items():

            if count > MAX_PER_STRUCTURAL_GROUP:
                errors.append(
                    f"{tf}:STRUCTURAL_GROUP_LIMIT:"
                    f"{group[0]}:"
                    f"{group[1]}:"
                    f"{count}"
                )

    return sorted(set(errors))


def make_manifest(
    candidates: list[dict[str, Any]],
    memory_reference: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:

    errors = validate_wave_candidates(
        candidates
    )

    if errors:
        raise ValueError(
            "WAVE_VALIDATION_FAILED:"
            + "|".join(errors)
        )

    immutable_candidate_rows = []

    for candidate in candidates:

        row = dict(candidate)

        # Never allow result/evidence fields
        # inside a pre-execution wave seal.
        forbidden_result_keys = (
            "hard_pass_result",
            "walk_forward_result",
            "cost_stress_result",
            "backtest_result",
            "profit",
            "pnl",
            "passed",
            "promotion",
        )

        found = [
            key
            for key in forbidden_result_keys
            if key in row
        ]

        if found:
            raise ValueError(
                "RESULT_FIELD_FORBIDDEN:"
                + ",".join(sorted(found))
            )

        row["sealed_identity"] = (
            candidate_identity(candidate)
        )

        immutable_candidate_rows.append(
            row
        )

    payload = {
        "schema": SCHEMA,

        "created_at_utc":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "timeframes":
            list(TRADING_TIMEFRAMES),

        "rules": {
            "candidate_limit_per_tf":
                MAX_CANDIDATES_PER_TF,

            "max_structural_groups_per_tf":
                MAX_STRUCTURAL_GROUP_COUNT,

            "max_per_structural_group":
                MAX_PER_STRUCTURAL_GROUP,

            "same_wave_result_driven_mutation":
                False,

            "search_until_winner":
                False,

            "validation_threshold_change":
                False,

            "entry_exit_joint_parameter_search":
                False,

            "result_fields_present":
                False,
        },

        "memory_reference":
            memory_reference,

        "memory_reference_hash":
            sha256_json(
                memory_reference
            ),

        "provenance":
            provenance,

        "candidates":
            immutable_candidate_rows,
    }

    # wave_id excludes created_at, so same exact
    # research payload gets same deterministic ID.
    identity_payload = dict(payload)

    identity_payload.pop(
        "created_at_utc",
        None,
    )

    wave_hash = sha256_json(
        identity_payload
    )

    payload["wave_hash"] = wave_hash

    payload["wave_id"] = (
        "EHW_"
        + wave_hash[:20]
    )

    return payload


def seal_create_only(
    manifest: dict[str, Any],
    output_dir: Path = RESULT_ROOT,
) -> Path:

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    wave_id = manifest["wave_id"]

    target = (
        output_dir
        / f"{wave_id}.json"
    )

    if target.exists():
        existing = json.loads(
            target.read_text(
                encoding="utf-8"
            )
        )

        if (
            existing.get("wave_hash")
            != manifest.get("wave_hash")
        ):
            raise RuntimeError(
                "EXISTING_WAVE_ID_HASH_MISMATCH"
            )

        # Create-only semantics:
        # never overwrite even when identical.
        return target

    text = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ) + "\n"

    fd, temp_name = tempfile.mkstemp(
        prefix=".wave_tmp_",
        suffix=".json",
        dir=str(output_dir),
        text=True,
    )

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(
                handle.fileno()
            )

        # Atomic create-only.
        try:
            os.link(
                temp_name,
                target,
            )
        except FileExistsError:
            pass

    finally:
        try:
            os.unlink(
                temp_name
            )
        except FileNotFoundError:
            pass

    if not target.exists():
        raise RuntimeError(
            "ATOMIC_WAVE_CREATE_FAILED"
        )

    return target


def self_test() -> None:

    candidates = []

    # 4 structural groups × 3 = 12 candidates
    groups = (
        ("CHANNEL_Z", "NONE"),
        ("CHANNEL_Z", "PERSIST_2"),
        ("TREND_Z", "NONE"),
        ("TREND_Z", "PRICE_DIRECTION"),
    )

    n = 0

    for mode, confirm_mode in groups:
        for k in range(3):

            n += 1

            candidates.append({
                "timeframe": "6H",
                "mode": mode,
                "confirm_mode": confirm_mode,
                "window": 16 + n,
                "threshold": 0.5 + (k * 0.1),
                "fingerprint":
                    f"SELFTEST_{n:02d}",
                "rationale":
                    "SELF_TEST_ONLY",
                "provenance":
                    "SELF_TEST",
            })

    manifest = make_manifest(
        candidates=candidates,
        memory_reference={
            "schema":
                "SELF_TEST_MEMORY",
            "record_count":
                0,
        },
        provenance={
            "source":
                "SELF_TEST",
            "research_execution":
                False,
        },
    )

    assert manifest["wave_id"].startswith(
        "EHW_"
    )

    assert len(
        manifest["candidates"]
    ) == 12

    assert (
        validate_wave_candidates(
            candidates
        )
        == []
    )

    duplicate = list(candidates)

    duplicate.append(
        dict(candidates[0])
    )

    assert validate_wave_candidates(
        duplicate
    )

    bad_result = dict(
        candidates[0]
    )

    bad_result["hard_pass_result"] = {
        "pass": True
    }

    try:
        make_manifest(
            candidates=[bad_result],
            memory_reference={},
            provenance={},
        )
    except ValueError:
        result_field_reject = True
    else:
        result_field_reject = False

    assert result_field_reject

    print("SCHEMA=", SCHEMA)
    print("SELF_TEST=PASS")
    print("MAX_CANDIDATES_PER_TF=12")
    print(
        "MAX_STRUCTURAL_GROUP_COUNT=4"
    )
    print(
        "MAX_PER_STRUCTURAL_GROUP=3"
    )
    print(
        "DUPLICATE_SAME_TF_REJECT=PASS"
    )
    print(
        "RESULT_FIELD_REJECT=PASS"
    )
    print(
        "CREATE_ONLY_SEMANTICS=YES"
    )
    print(
        "SAME_WAVE_MUTATION=FORBIDDEN"
    )
    print(
        "SEARCH_UNTIL_WINNER=FORBIDDEN"
    )
    print("RESEARCH_EXECUTION=NO")
    print("HARD_PASS_EXECUTION=NO")
    print("KNOWLEDGE_WRITE=NO")
    print("ORDER_WRITE=NO")
    print("POSITION_CHANGE=NO")
    print("FROZEN_CORE_MODIFIED=NO")
    print("SERVER_RELEASE=NO")


if __name__ == "__main__":
    self_test()
