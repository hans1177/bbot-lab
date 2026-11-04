# 파일명: strategy_lab/first_final_pass_report.py
"""Read-only certification progress: a passing research run is NOT a certified strategy.

This reporter never changes canonical policy, research evidence, or live state.
It distinguishes research gates from downstream exact-instance shadow/release.
"""
import argparse
import json
import math
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
    # Diagnostic-only; aggregate former exact versions too, without gate reuse.
    full_gate_reasons=Counter(); full_gate_families=Counter()
    full_reviewed=0; full_failed=0
    robust_measured=0; robust_fail=Counter()
    concentrated_measured=0; concentrated_fail=Counter()
    stress_status=Counter()
    for row in entries:
        if not isinstance(row, dict) or row.get("eligible") is not True:
            continue
        stage = str(row.get("pipeline_stage") or "")
        counts[stage] += 1
        full_gate = row.get("full_validation_gate") or {}
        blind_gate = row.get("sealed_final_blind_gate") or {}
        if not isinstance(full_gate,dict): full_gate={}
        if not isinstance(blind_gate,dict): blind_gate={}
        if full_gate.get("decision") in ("PASS","FAIL","COMPENSATION_REVIEW"):
            full_reviewed+=1
            if full_gate.get("pass") is not True:
                full_failed+=1
                codes=full_gate.get("absolute_fail_reasons") or []
                if not isinstance(codes,list):codes=[]
                families=set()
                for reason in codes:
                    code=(reason if isinstance(reason,str) and
                          reason.startswith("FULL_VALIDATION_") and
                          len(reason)<=100 and reason.replace("_","").isalnum()
                          else "OTHER_OR_UNRECORDED")
                    full_gate_reasons[code]+=1
                    if "COST_STRESS" in code: families.add("COST")
                    elif "WF_OOS" in code: families.add("OUT_OF_SAMPLE_RETURN")
                    elif "ROBUSTNESS" in code or "NEIGHBOR" in code or "CONCENTRATION" in code:
                        families.add("OVERFIT_OR_CONCENTRATION")
                    elif "REGIME" in code or "LONG_SHORT" in code: families.add("MARKET_REGIME")
                    elif "FINAL_RISK" in code: families.add("FINAL_RISK")
                    elif "MINIMUM_TRADES" in code: families.add("INSUFFICIENT_TRADES")
                    elif "MISSING" in code or "INVALID" in code: families.add("MISSING_OR_INVALID_DATA")
                    else: families.add("OTHER_RECORDED_FAILURE")
                if not families: families.add("NO_ABSOLUTE_REASON_OR_COMPENSATION")
                full_gate_families.update(families)
            metrics=full_gate.get("metrics") or {}
            if not isinstance(metrics,dict):metrics={}
            ratio=metrics.get("positive_robustness_slice_ratio")
            if isinstance(ratio,(float,int)) and not isinstance(ratio,bool) and math.isfinite(ratio):
                robust_measured+=1
                for threshold in (0.40,0.50,0.60):
                    if ratio < threshold-1e-12: robust_fail[f"{threshold:.2f}"]+=1
            share=metrics.get("top_5pct_trade_net_profit_share")
            if isinstance(share,(float,int)) and not isinstance(share,bool) and math.isfinite(share):
                concentrated_measured+=1
                for threshold in (0.60,0.70,0.80):
                    if share > threshold+1e-12: concentrated_fail[f"{threshold:.2f}"]+=1
            stress=metrics.get("cost_stress_2x_expectancy_pct")
            if not isinstance(stress,(float,int)) or isinstance(stress,bool) or not math.isfinite(stress):
                stress_status["NOT_RECORDED"]+=1
            else:
                stress_status["POSITIVE" if stress>0 else "NONPOSITIVE"]+=1
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
        "aws_final_certification": "NOT_VERIFIED_BY_GITHUB_RESEARCH",
        "research_full_and_sealed_blind_pass_count": len(final_gates),
        "research_full_and_sealed_blind_pass": final_gates[:8],
        "unverified_shadow_stage_count": len(invalid_shadow),
        "stage_counts": dict(sorted(counts.items())),
        "pipeline_entries_seen": len(entries),
        "full_validation_failure_diagnostics": {
            "schema":"B_BOT_FULL_GATE_DIAGNOSTICS_READ_ONLY_V1",
            "reviewed_versions":full_reviewed,"failed_versions":full_failed,
            "absolute_reasons":dict(sorted(full_gate_reasons.items())),
            "failure_families":dict(sorted(full_gate_families.items())),
            "exact_version_counts_not_independent_strategy_count":True,
        },
        "threshold_sensitivity_observation_only": {
            "schema":"B_BOT_FULL_GATE_SENSITIVITY_READ_ONLY_V1",
            "positive_robustness_slice_ratio": {
                "measured":robust_measured,
                "violations_min_40pct":robust_fail["0.40"],
                "violations_current_min_50pct":robust_fail["0.50"],
                "violations_min_60pct":robust_fail["0.60"],
            },
            "top_5pct_trade_profit_share": {
                "measured":concentrated_measured,
                "violations_max_60pct":concentrated_fail["0.60"],
                "violations_current_max_70pct":concentrated_fail["0.70"],
                "violations_max_80pct":concentrated_fail["0.80"],
            },
            "cost_stress_2x":dict(sorted(stress_status.items())),
            "policy_changed":False,
            "sealed_blind_exposure_used":False,
            "cannot_estimate_combined_gate_pass_from_single_threshold":True,
        },
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
            "**완전 인증: GitHub 연구실 단독으로 확정 불가 (AWS 별도 검증 필요)**",
            "",
            "- 연구 전체 검증 + 봉인 블라인드 통과: " +
                str(report["research_full_and_sealed_blind_pass_count"]),
            "- AWS 섀도 검증 대기 인계 후보: " +
                str(report["handoff_awaiting_server_validation_count"]),
            "- 잘못된 LOCAL_SHADOW_PENDING 증거: " +
                str(report["unverified_shadow_stage_count"]),
            "- 상태: " + report["first_final_pass_status"],
            "- 전체검증 실패 분류: " +
                json.dumps(report["full_validation_failure_diagnostics"]["failure_families"],sort_keys=True),
            "- 기준별 민감도(합격 판정 아님): " +
                json.dumps(report["threshold_sensitivity_observation_only"],sort_keys=True),
            "- 가장 앞선 추적손절 검증: " +
                json.dumps(report["closest_trailing"][:1], ensure_ascii=False),
            "",
            "연구 실행 성공만으로 전략 최종 통과를 선언하지 않음.",
        ]
        with Path(summary).open("a", encoding="utf-8") as target:
            target.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
