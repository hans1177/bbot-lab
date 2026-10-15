# standard_version_registry_v1.py
# Immutable STANDARD V1/V2 version registry initializer.
# Does not modify strategy source files and does not deploy/restart anything.

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
REGISTRY_DIR = RESULTS / "standard_versions"

V1_RECORD = REGISTRY_DIR / "STANDARD_V1.json"
V2_RECORD = REGISTRY_DIR / "STANDARD_V2.json"
INDEX_JSONL = REGISTRY_DIR / "standard_versions_index.jsonl"
LATEST_JSON = REGISTRY_DIR / "standard_versions_latest.json"

V1_METRICS = {
    "final_equity": 188700719.65,
    "compound_pct": 1886907.20,
    "realized_mdd_pct": -83.99704085937681,
    "liquidations": 0,
    "entries": 16379,
    "entry_checks": 128368,
    "entry_blocks": 111989,
    "control_sl_checks": 58118,
    "control_sl_breaches": 2683,
    "control_sl_closes": 2724,
    "trailing_closes": 11812,
    "state_reentries": 13881,
    "multi_tf_skips": 443,
    "repro_status": "PASS",
}

V2_METRICS = {
    "final_equity": 5494970540.99,
    "compound_pct": 54949605.41,
    "realized_mdd_pct": -65.431842,
    "liquidations": 0,
    "entries": 16702,
    "entry_checks": 28360,
    "entry_blocks": 11658,
    "control_sl_checks": 58115,
    "control_sl_breaches": 526,
    "control_sl_closes": 531,
    "trailing_closes": 14210,
    "state_reentries": 16493,
    "state_reentry_cancels": 119,
    "state_reentry_skips": 11322,
    "multi_tf_skips": 38,
    "fast_frozen_parity": "PASS",
    "zero_liq_survive": "PASS",
    "original_exact": "PASS",
    "historical_validation": "PASS",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_hash(obj: Any) -> str:
    raw = json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load_json_if_exists(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def must_write_immutable(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if canonical_hash(existing) == canonical_hash(payload):
            print(f"[KEEP] immutable record already identical: {path}")
            return
        raise RuntimeError(
            f"IMMUTABLE RECORD EXISTS WITH DIFFERENT CONTENT: {path}\n"
            "Do not overwrite an existing STANDARD version record."
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)
    print(f"[WRITE] {path}")


def append_index_once(record: dict[str, Any]) -> None:
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)

    existing_versions = set()
    if INDEX_JSONL.exists():
        for line in INDEX_JSONL.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                existing_versions.add(row.get("version"))
            except Exception:
                pass

    if record["version"] in existing_versions:
        print(f"[KEEP] index already contains {record['version']}")
        return

    with INDEX_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"[APPEND] {INDEX_JSONL} <- {record['version']}")


def file_hashes() -> dict[str, str | None]:
    paths = {
        "standard_profile_json": ROOT / "standard_profile.json",
        "standard_baseline_json": ROOT / "standard_baseline.json",
        "standard_runner_py": ROOT / "standard_runner.py",
        "leverage_scan_py": ROOT / "leverage_scan.py",
        "fast_engine_v1_py": ROOT / "experiments" / "fast_engine_v1.py",
        "research_pipeline_v1_py": ROOT / "experiments" / "research_pipeline_v1.py",
        "v2_candidate_freeze_json":
            ROOT / "results" / "research_pipeline_v2"
            / "standard_v2_candidate_freeze_latest.json",
        "v2_original_exact_json":
            ROOT / "results" / "research_pipeline_v2"
            / "original_exact_u30_latest.json",
        "v2_validation_json":
            ROOT / "results" / "research_pipeline_v2"
            / "standard_v2_validation_latest.json",
    }
    return {name: sha256_file(path) for name, path in paths.items()}


def build_v1(hashes: dict[str, str | None]) -> dict[str, Any]:
    record = {
        "schema": "STANDARD_VERSION_RECORD_V1",
        "version": "STANDARD_V1",
        "status": "SEALED",
        "historical_validation": "PASS",
        "strategy": {
            "owner_frames": ["12H", "1D"],
            "signal": "EMA_CROSS + SAR",
            "leverage": {"12H": 10, "1D": 15},
            "universe_policy_historical": "U35",
            "trailing": {"start_pct": 4.0, "callback_pct": 2.0},
            "opposite_exit_source": "STANDARD_COMBINED",
            "opposite_reopen_timing": "IMMEDIATE",
            "entry_gate": "STANDARD_CROSS_ENTRY_GATE",
            "control_sl": "STANDARD_CONTROL_SL",
            "state_reentry": "STANDARD_STATE_REENTRY",
        },
        "metrics": V1_METRICS,
        "source_hashes": {
            "standard_profile_json": hashes["standard_profile_json"],
            "standard_baseline_json": hashes["standard_baseline_json"],
            "standard_runner_py": hashes["standard_runner_py"],
            "leverage_scan_py": hashes["leverage_scan_py"],
        },
        "notes": [
            "Original exact STANDARD V1 baseline is sealed.",
            "This record must never be overwritten by later STANDARD versions.",
        ],
    }
    record["record_hash"] = canonical_hash(record)
    return record


def build_v2(hashes, freeze, exact, validation) -> dict[str, Any]:
    strategy = {
        "owner_frames": ["12H", "1D"],
        "signal": "EMA_CROSS + SAR",
        "leverage": {"12H": 10, "1D": 15},
        "universe_policy_historical": "FROZEN_U30",
        "live_universe_policy_planned": {
            "mode": "DYNAMIC_TOP_N",
            "top_n": 30,
            "note": (
                "Operational live policy. Historical validation used the "
                "frozen U30 set; keep this distinction in records."
            ),
        },
        "trailing": {"start_pct": 3.0, "callback_pct": 1.5},
        "opposite_exit_source": "STANDARD_COMBINED",
        "opposite_reopen_timing": "NEXT_1H_STATE",
        "entry_gate": "STANDARD_CROSS_ENTRY_GATE",
        "control_sl": "STANDARD_CONTROL_SL",
        "state_reentry": "STANDARD_STATE_REENTRY",
    }

    if freeze and isinstance(freeze.get("strategy"), dict):
        strategy["frozen_pipeline_strategy"] = freeze["strategy"]

    record = {
        "schema": "STANDARD_VERSION_RECORD_V1",
        "version": "STANDARD_V2",
        "status": "HISTORICAL_VALIDATION_PASS",
        "candidate": "T1_TIGHT_BOTH_3_1P5__NEXT_1H_STATE__U30",
        "freeze_hash": freeze.get("freeze_hash") if freeze else None,
        "historical_validation": {
            "walk_forward": "PASS",
            "robustness_confirm": "PASS",
            "original_exact": "PASS",
            "fast_frozen_parity": "PASS",
            "zero_liquidation_survive": "PASS",
        },
        "strategy": strategy,
        "metrics": V2_METRICS,
        "comparison_vs_v1": {
            "final_equity_ratio":
                V2_METRICS["final_equity"] / V1_METRICS["final_equity"],
            "mdd_improvement_percentage_points":
                V2_METRICS["realized_mdd_pct"]
                - V1_METRICS["realized_mdd_pct"],
        },
        "source_hashes": {
            "standard_profile_json": hashes["standard_profile_json"],
            "standard_baseline_json": hashes["standard_baseline_json"],
            "standard_runner_py": hashes["standard_runner_py"],
            "leverage_scan_py": hashes["leverage_scan_py"],
            "fast_engine_v1_py": hashes["fast_engine_v1_py"],
            "research_pipeline_v1_py": hashes["research_pipeline_v1_py"],
            "candidate_freeze_json": hashes["v2_candidate_freeze_json"],
            "original_exact_json": hashes["v2_original_exact_json"],
            "validation_json": hashes["v2_validation_json"],
        },
        "pipeline_records": {
            "candidate_freeze": freeze,
            "original_exact_summary": exact,
            "validation_summary": validation,
        },
        "notes": [
            "STANDARD source files were not modified during research validation.",
            "Historical validation used frozen U30.",
            "Planned live runner policy is Dynamic Top 30 via strategy config.",
            "Dynamic Top-N operational policy must be recorded separately from historical U30 validation.",
        ],
    }
    record["record_hash"] = canonical_hash(record)
    return record


def main() -> None:
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)

    hashes = file_hashes()

    freeze_path = (
        ROOT / "results" / "research_pipeline_v2"
        / "standard_v2_candidate_freeze_latest.json"
    )
    exact_path = (
        ROOT / "results" / "research_pipeline_v2"
        / "original_exact_u30_latest.json"
    )
    validation_path = (
        ROOT / "results" / "research_pipeline_v2"
        / "standard_v2_validation_latest.json"
    )

    freeze = load_json_if_exists(freeze_path)
    exact = load_json_if_exists(exact_path)
    validation = load_json_if_exists(validation_path)

    v1 = build_v1(hashes)
    v2 = build_v2(hashes, freeze, exact, validation)

    must_write_immutable(V1_RECORD, v1)
    must_write_immutable(V2_RECORD, v2)

    append_index_once({
        "version": "STANDARD_V1",
        "status": v1["status"],
        "record_path": str(V1_RECORD),
        "record_hash": v1["record_hash"],
        "metrics": v1["metrics"],
    })
    append_index_once({
        "version": "STANDARD_V2",
        "status": v2["status"],
        "record_path": str(V2_RECORD),
        "record_hash": v2["record_hash"],
        "freeze_hash": v2["freeze_hash"],
        "metrics": v2["metrics"],
        "comparison_vs_v1": v2["comparison_vs_v1"],
    })

    latest = {
        "schema": "STANDARD_VERSION_REGISTRY_LATEST",
        "generated_at_utc": utc_now(),
        "sealed_versions": ["STANDARD_V1", "STANDARD_V2"],
        "current_historical_champion": "STANDARD_V2",
        "records": {
            "STANDARD_V1": {
                "path": str(V1_RECORD),
                "record_hash": v1["record_hash"],
            },
            "STANDARD_V2": {
                "path": str(V2_RECORD),
                "record_hash": v2["record_hash"],
            },
        },
    }

    LATEST_JSON.write_text(
        json.dumps(latest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print()
    print("=" * 88)
    print("STANDARD VERSION REGISTRY")
    print("=" * 88)
    print("V1 =", V1_RECORD)
    print("V2 =", V2_RECORD)
    print("INDEX =", INDEX_JSONL)
    print("LATEST =", LATEST_JSON)
    print("V1 HASH =", v1["record_hash"])
    print("V2 HASH =", v2["record_hash"])
    print(
        "V2 / V1 FINAL EQUITY =",
        f"{v2['comparison_vs_v1']['final_equity_ratio']:.4f}x",
    )
    print(
        "MDD IMPROVEMENT =",
        f"{v2['comparison_vs_v1']['mdd_improvement_percentage_points']:+.6f}pp",
    )
    print("STATUS = PASS")


if __name__ == "__main__":
    main()
