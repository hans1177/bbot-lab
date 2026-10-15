# 파일명: strategy_lab/v3_sealed_entry_wave_executor.py
from __future__ import annotations

import gc
import hashlib
import json
import os
import re
from pathlib import Path

import v3_entry_hypothesis_wave_seal as seal
import v3_hypothesis_factory as hf


DEFAULT_WAVE_ID = "EHW_3eed1860aee3e9cc4541"

_requested_wave_id = str(
    os.environ.get(
        "BBOT_SEALED_WAVE_ID",
        DEFAULT_WAVE_ID,
    )
).strip()

if not re.fullmatch(
    r"EHW_[0-9a-fA-F]{20}",
    _requested_wave_id,
):
    raise RuntimeError(
        "INVALID_SEALED_WAVE_ID"
    )

WAVE_ID = _requested_wave_id

WAVE_PATH = (
    Path(__file__).resolve().parent
    / "results"
    / "v3"
    / "entry_hypothesis_waves"
    / f"{WAVE_ID}.json"
)

DEFAULT_MAX_RUNS = 6


def load_wave():
    data = json.loads(
        WAVE_PATH.read_text(
            encoding="utf-8"
        )
    )

    if data.get("wave_id") != WAVE_ID:
        raise RuntimeError(
            "SEALED_WAVE_ID_MISMATCH"
        )

    payload = {key: value for key, value in data.items() if key not in {"wave_id", "wave_hash", "created_at_utc"}}
    if seal.sha256_json(payload) != data.get("wave_hash"):
        raise RuntimeError("SEALED_WAVE_CONTENT_HASH_MISMATCH")
    for candidate in data.get("candidates") or []:
        if candidate.get("source_sha256"):
            source = (hf.ROOT / candidate["canonical_source_file"]).resolve()
            source.relative_to(hf.ROOT.resolve())
            if hashlib.sha256(source.read_bytes()).hexdigest() != candidate["source_sha256"]:
                raise RuntimeError("SEALED_CANDIDATE_SOURCE_CHANGED")

    rows = data.get("candidates")

    if not isinstance(rows, list):
        raise RuntimeError(
            "SEALED_CANDIDATES_MISSING"
        )

    if not rows:
        raise RuntimeError(
            "SEALED_CANDIDATES_EMPTY"
        )

    errors = seal.validate_wave_candidates(
        rows
    )

    if errors:
        raise RuntimeError(
            "SEALED_VALIDATION_FAIL:"
            + "|".join(errors)
        )

    return data


def pending_candidates(wave):
    done = hf.completed_pairs()

    out = []

    for row in wave["candidates"]:
        tf = str(
            row["timeframe"]
        ).upper()

        name = str(
            row["name"]
        ).upper()

        if (name, tf) not in done:
            out.append(
                dict(row)
            )

    return out


def run_candidate(candidate):
    return hf.run_one(candidate=candidate)



def main():
    wave = load_wave()

    try:
        max_runs = int(
            os.environ.get(
                "BBOT_SEALED_MAX_RUNS",
                str(DEFAULT_MAX_RUNS),
            )
        )
    except Exception:
        max_runs = DEFAULT_MAX_RUNS

    max_runs = max(
        1,
        min(max_runs, 72),
    )

    pending_before = pending_candidates(
        wave
    )

    print(
        "WAVE=",
        WAVE_ID,
    )
    print(
        "TOTAL=72",
        "PENDING=",
        len(pending_before),
        "BATCH_MAX=",
        max_runs,
    )

    if not pending_before:
        print("STATUS=SEALED_WAVE_COMPLETE")
        return

    executed = 0
    hp_pass = 0
    failed = 0

    for candidate in pending_before[:max_runs]:

        tf = str(
            candidate["timeframe"]
        ).upper()

        name = str(
            candidate["name"]
        ).upper()

        try:
            result = run_candidate(
                candidate
            )
        except Exception as exc:
            print(
                f"[STOP] {tf} {name} "
                f"EXECUTOR_ERROR="
                f"{type(exc).__name__}:"
                f"{exc}"
            )
            break

        executed += 1

        ev = (
            result.get("evaluation")
            or {}
        )

        hp = bool(
            ev.get(
                "hard_pass",
                ev.get("pass"),
            )
        )

        if hp:
            hp_pass += 1
        else:
            failed += 1

        wf = ev.get(
            "walk_forward_avg_net_pct"
        )

        stress = ev.get(
            "walk_forward_cost_stress_2x_avg_net_pct"
        )

        print(
            f"[{executed}] "
            f"{tf} "
            f"{name} "
            f"STATUS={result.get('status')} "
            f"HP={hp} "
            f"WF={wf} "
            f"STRESS={stress} "
            f"STAGE={result.get('pipeline_stage')}"
        )

        gc.collect()

    pending_after = pending_candidates(
        wave
    )

    print("\n=== BATCH SUMMARY ===")
    print("EXECUTED=", executed)
    print("HARD_PASS=", hp_pass)
    print("NON_HARD_PASS=", failed)
    print(
        "REMAINING=",
        len(pending_after),
    )

    if not pending_after:
        print(
            "SEALED_WAVE_COMPLETE=YES"
        )
    else:
        print(
            "SEALED_WAVE_COMPLETE=NO"
        )

    print("SEALED_WAVE_MUTATED=NO")
    print("AI_CALL=NO")
    print("KNOWLEDGE_WRITE=YES")
    print("ORDER_WRITE=NO")
    print("POSITION_CHANGE=NO")
    print("FROZEN_CORE_MODIFIED=NO")
    print("SERVER_RELEASE=NO")


if __name__ == "__main__":
    main()
