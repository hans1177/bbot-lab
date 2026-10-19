"""Read-only certification progress: a passing research run is NOT a certified strategy.

This reporter never changes canonical policy, research evidence, or live state.
It distinguishes research gates from downstream exact-instance shadow/release.
"""
import argparse
import json
import os
from collections import Counter
from pathlib import Path

PIPELINE = "strategy_lab/results/v3/historical_314_pipeline_stage2.json"
TRAILING = "strategy_lab/results/v3/individual_trailing_stage3.json"


def read_json(path):
    if path.is_symlink() or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeError):
        return {}


def certification_progress(root, manifest_path):
    root = Path(root)
    pipeline = read_json(root / PIPELINE)
    entries = pipeline.get("entries") or []
    if not isinstance(entries, list):
        raise RuntimeError("P0_PIPELINE_ENTRIES_INVALID")
    trailing_state = read_json(root / TRAILING)
    trailing_entries = trailing_state.get("entries") or {}
    if not isinstance(trailing_entries, dict):
        trailing_entries = {}
    counts = Counter()
    final_gates = []
    invalid_shadow = []
    nearest = []
    for row in entries:
        if not isinstance(row, dict) or row.get("eligible") is not True:
            continue
        stage = str(row.get("pipeline_stage") or "")
        counts[stage] += 1
        full_gate = row.get("full_validation_gate") or {}
        blind_gate = row.get("sealed_final_blind_gate") or {}
        validated = (isinstance(full_gate, dict) and full_gate.get("pass") is True
                     and isinstance(blind_gate, dict) and blind_gate.get("pass") is True
                     and blind_gate.get("next_stage") == "LOCAL_SHADOW_PENDING")
        identity = str(row.get("canonical_identity_key") or "")
        exact = str(row.get("exact_strategy_version") or "")
        strategy = str(row.get("strategy_id") or "")
        if stage == "LOCAL_SHADOW_PENDING":
            if validated and identity and exact and strategy:
                final_gates.append({"strategy": strategy, "version": exact,
                                    "timeframe": str(row.get("timeframe") or "")})
            else:
                invalid_shadow.append(exact or "UNKNOWN")
        elif stage == "INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING" and identity and exact:
            item = trailing_entries.get(identity) or {}
            if not isinstance(item, dict):
                item = {}
            fine = item.get("fine_candidates") or []
            done = item.get("fine_results") or {}
            if not isinstance(fine, list) or not isinstance(done, dict):
                fine, done = [], {}
            completed = sum(
                isinstance(d, dict) and str(d.get("status") or "") == "MEASURED"
                for d in done.values()
            )
            nearest.append({"strategy": strategy, "version": exact,
                            "timeframe": str(row.get("timeframe") or ""),
                            "fine_measured": completed, "fine_planned": len(fine),
                            "trailing_stage": str(item.get("status") or "NOT_STARTED")})

    manifest = read_json(Path(manifest_path))
    handed = manifest.get("candidates") or []
    if not isinstance(handed, list):
        raise RuntimeError("P0_MANIFEST_CANDIDATES_INVALID")
    handoff = [
        {"strategy": str(row.get("strategy_id") or ""),
         "version": str(row.get("exact_strategy_version") or ""),
         "timeframe": str(row.get("timeframe") or "")}
        for row in handed if isinstance(row, dict)
        and row.get("server_shadow_required") is True
        and row.get("approved_for_server_release") is False
    ]
    # A research-side report can never certify server shadow or canonical release.
    return {
        "schema": "B_BOT_P0_CERTIFICATION_PROGRESS_V1",
        "objective": "FIRST_END_TO_END_FINAL_PASS",
        "research_run_success_not_certification": True,
        "final_certified_count": 0,
        "research_full_and_sealed_blind_pass_count": len(final_gates),
        "research_full_and_sealed_blind_pass": final_gates[:8],
        "unverified_shadow_stage_count": len(invalid_shadow),
        "stage_counts": dict(sorted(counts.items())),
        "closest_trailing": sorted(nearest,
              key=lambda r: (-r["fine_measured"], r["fine_planned"], r["version"]))[:5],
        "handoff_awaiting_server_validation_count": len(handoff),
        "handoff_awaiting_server_validation": handoff[:8],
        "first_final_pass_status": (
            "SHADOW_AND_CANONICAL_RELEASE_PENDING" if final_gates or handoff
            else "NO_FINAL_VALIDATED_STRATEGY_YET"
        ),
        "server_orders_touched": False,
        "server_mutated": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    report = certification_progress(args.root, args.manifest)
    print("P0_FINAL_CERTIFICATION_PROGRESS=" + json.dumps(report, sort_keys=True, ensure_ascii=False))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        lines = [
            "### P0 — 첫 최종 인증 전략",
            "",
            "**완전 인증 완료: 0건 (GitHub 연구실 단독으로 AWS 인증을 확정하지 않음)**",
            "",
            "- 연구 전체 검증 + 봉인 블라인드 통과: " +
                str(report["research_full_and_sealed_blind_pass_count"]),
            "- AWS 섀도 검증 대기 인계 후보: " +
                str(report["handoff_awaiting_server_validation_count"]),
            "- 잘못된 LOCAL_SHADOW_PENDING 증거: " +
                str(report["unverified_shadow_stage_count"]),
            "- 상태: " + report["first_final_pass_status"],
            "- 가장 앞선 추적손절 검증: " +
                json.dumps(report["closest_trailing"][:1], ensure_ascii=False),
            "",
            "연구 실행 성공만으로 전략 최종 통과를 선언하지 않음.",
        ]
        with Path(summary).open("a", encoding="utf-8") as target:
            target.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
