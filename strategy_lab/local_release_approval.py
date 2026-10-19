# -*- coding: utf-8 -*-
# 파일명: strategy_lab/local_release_approval.py
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.getenv("BBOT_LOCAL_ROOT", r"C:\my\bottest"))
LAB = ROOT / "strategy_lab"
RESULTS = LAB / "results"
V3 = RESULTS / "v3"

sys.path.insert(0, str(LAB))

import v3_governance as governance


STATE = V3 / "local_release_validation.json"

APPROVAL = (
    ROOT
    / "release"
    / "approved_runtime_release.json"
)

CLOSURE = (
    ROOT
    / "runtime_release_dependency_closure.json"
)

STRATEGY_CONFIG = ROOT / "strategy_config.json"

ORDER = ROOT / "order.py"

EXPECTED_ORDER_HASH = (
    "513e85ab9935e8ea107f5f26dcb359f"
    "382c8deddc63c90d8bcf80348e45ec86c"
)


def now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def sha256(path):
    h = hashlib.sha256()

    with Path(path).open("rb") as f:
        for chunk in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(chunk)

    return h.hexdigest()


def atomic(path, obj):
    path = Path(path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp = path.with_suffix(
        path.suffix + ".tmp"
    )

    tmp.write_text(
        json.dumps(
            obj,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        ) + "\n",
        encoding="utf-8",
    )

    os.replace(tmp, path)


def load(path, default):
    try:
        return json.loads(
            Path(path).read_text(
                encoding="utf-8-sig"
            )
        )
    except Exception:
        return default


def _extract_candidate_rows(obj, path="$", depth=0):
    """
    persisted research state ????
    strategy + timeframe + evaluation ??? ?? ????.
    """

    out = []

    if depth > 16:
        return out

    if isinstance(obj, dict):

        strategy = obj.get("strategy")
        timeframe = obj.get("timeframe")
        evaluation = obj.get("evaluation")

        if (
            strategy
            and timeframe
            and isinstance(evaluation, dict)
        ):
            row = dict(obj)
            row["_evidence_locator"] = path
            out.append(row)

        for key, value in obj.items():

            if isinstance(value, (dict, list)):
                out.extend(
                    _extract_candidate_rows(
                        value,
                        f"{path}.{key}",
                        depth + 1,
                    )
                )

    elif isinstance(obj, list):

        for i, value in enumerate(obj):

            if isinstance(value, (dict, list)):
                out.extend(
                    _extract_candidate_rows(
                        value,
                        f"{path}[{i}]",
                        depth + 1,
                    )
                )

    return out


def source_file_for(row):

    candidates = (
        row.get("family_file"),
        row.get("strategy_file"),
        row.get("source_file"),
    )

    for raw in candidates:

        raw = str(raw or "").strip()

        if not raw:
            continue

        path = Path(raw)

        if not path.is_absolute():
            path = ROOT / path

        try:
            path = path.resolve()
            path.relative_to(ROOT.resolve())
        except Exception:
            continue

        if (
            path.is_file()
            and path.suffix.lower() == ".py"
        ):
            return path

    return None


def candidate_result_files():
    """
    ?? ?? ??? ?? BROAD json? ???
    persisted evaluation ???? ????.

    ?? ?? Trial? PASS ??? ????.
    ?? Trial? FAIL?? ?? ????.
    """

    evidence_files = [
        RESULTS
        / "v3"
        / "research_knowledge.json",

        RESULTS
        / "v3"
        / "roadmap_queue.json",
    ]

    rows = []
    seen = set()

    for path in evidence_files:

        if not path.is_file():
            continue

        obj = load(
            path,
            None,
        )

        if obj is None:
            continue

        for row in _extract_candidate_rows(
            obj
        ):

            try:
                trial = int(
                    row.get("trial")
                    or -1
                )
            except Exception:
                trial = -1

            strategy = str(
                row.get("strategy")
                or ""
            ).upper()

            timeframe = str(
                row.get("timeframe")
                or ""
            ).upper()

            key = (
                strategy,
                timeframe,
                trial,
            )

            if key in seen:
                continue

            seen.add(key)

            # ---------------------------------------------
            # FAIL / MEASURED / ??? ??? ?? ??
            # ---------------------------------------------

            if not valid_backtest(row):
                continue

            # Runtime source? ?? ?? ???
            # release candidate? ? ? ??.
            if source_file_for(row) is None:
                continue

            row = dict(row)

            row[
                "_evidence_file"
            ] = (
                path.relative_to(ROOT)
                .as_posix()
            )

            rows.append(
                (
                    trial,
                    path.stat().st_mtime_ns,
                    path,
                    row,
                )
            )

    rows.sort(
        key=lambda x: (
            x[0],
            x[1],
        ),
        reverse=True,
    )

    return [
        (
            mtime,
            path,
            row,
        )
        for trial, mtime, path, row
        in rows
    ]


def valid_backtest(row):

    evaluation = (
        row.get("evaluation")
        if isinstance(
            row.get("evaluation"),
            dict,
        )
        else {}
    )

    return (
        str(
            row.get("status") or ""
        ).upper() == "PASS"
        and evaluation.get("pass")
        is True
        and float(
            evaluation.get(
                "walk_forward_avg_net_pct"
            )
            or 0.0
        ) > 0.0
        and float(
            evaluation.get(
                "walk_forward_cost_stress_2x_avg_net_pct"
            )
            or 0.0
        ) > 0.0
    )


def evidence_file(version_id, name, strategy_instance_id=""):
    base = (
        V3
        / "validation"
        / str(version_id)
    )

    if (
        str(name).lower() == "shadow"
        and str(strategy_instance_id or "").strip()
    ):
        return base / (
            "shadow__"
            + str(strategy_instance_id).strip()
            + ".json"
        )

    return base / f"{name}.json"



def evidence_pass(
    version_id,
    name,
    strategy_instance_id="",
):
    path = evidence_file(
        version_id,
        name,
        strategy_instance_id,
    )

    obj = load(
        path,
        {},
    )

    if not isinstance(obj, dict):
        return False

    if obj.get("pass") is not True:
        return False

    if str(
        obj.get("version_id")
        or ""
    ) != str(version_id):
        return False

    # Shadow는 Strategy Instance에 귀속되는 로컬 runtime evidence다.
    if name == "shadow":

        if obj.get(
            "real_order"
        ) is not False:
            return False

        if obj.get(
            "server_mutation"
        ) is not False:
            return False

        if str(
            obj.get("role")
            or ""
        ).upper() != "LOCAL_RESEARCH":
            return False

        if str(strategy_instance_id or "").strip():
            if str(
                obj.get("strategy_instance_id")
                or ""
            ) != str(strategy_instance_id):
                return False

    return True



def research_architecture_v3():
    arch = (
        governance.constitution()
        .get("research_architecture_v3")
        or {}
    )

    if arch.get("policy_id") != (
        "RA_MAIN_MIDDLE_DETAIL_20260830_V1"
    ):
        raise RuntimeError(
            "RESEARCH_ARCHITECTURE_INVALID"
        )

    return arch


def minimal_shadow_pass(version_id, strategy_instance_id=""):
    # Shadow는 수익성 gate가 아니라 exact Strategy Instance runtime-safety gate다.
    arch = research_architecture_v3()

    shadow_policy = (
        arch.get("shadow")
        or {}
    )

    if shadow_policy.get(
        "mandatory_minimal_runtime_gate"
    ) is not True:
        return False

    if shadow_policy.get(
        "profitability_gate"
    ) is not False:
        return False

    if str(strategy_instance_id or "").strip():
        if shadow_policy.get("strategy_instance_scoped") is not True:
            return False

    if not evidence_pass(
        version_id,
        "shadow",
        strategy_instance_id,
    ):
        return False

    obj = load(
        evidence_file(
            version_id,
            "shadow",
            strategy_instance_id,
        ),
        {},
    )

    gate = (
        obj.get("minimal_runtime_gate")
        if isinstance(
            obj.get("minimal_runtime_gate"),
            dict,
        )
        else obj
    )

    if "runtime_errors" not in gate:
        return False

    try:
        runtime_errors = int(
            gate.get("runtime_errors")
        )
    except Exception:
        return False

    if runtime_errors != 0:
        return False

    required_runtime_checks = (
        "closed_bar_timing_pass",
        "signal_logic_parity_pass",
        "state_transition_pass",
        "order_simulation_cost_sanity_pass",
        "strategy_instance_policy_snapshot_pass",
        "order_quantity_calculation_pass",
        "duplicate_or_missing_action_integrity_pass",
        "portfolio_allocation_gate_integration_pass",
    )

    for key in required_runtime_checks:
        if gate.get(key) is not True:
            return False

    individual_policy_pass = (
        gate.get("individual_trailing_snapshot_binding_pass") is True
        and gate.get("individual_reentry_snapshot_binding_pass") is True
    )
    legacy_baseline_pass = (
        gate.get("policy_contract_mode") == "LEGACY_FROZEN_BASELINE_V1"
        and gate.get("legacy_frozen_baseline_policy_pass") is True
        and gate.get("individual_trailing_snapshot_binding_pass") is False
        and gate.get("individual_reentry_snapshot_binding_pass") is False
    )
    if not (individual_policy_pass or legacy_baseline_pass):
        return False

    try:
        lifecycle_count = int(
            gate.get("complete_lifecycles")
            or gate.get("lifecycle_count")
            or 0
        )
    except Exception:
        lifecycle_count = 0

    replay_substitute = (
        gate.get("replay_dryrun_state_transition_pass") is True
        and gate.get("live_closed_bar_confirmation_pass") is True
    )

    if lifecycle_count < 1 and not replay_substitute:
        return False

    return True




def status_for_gates(gates):

    if not gates["BACKTEST_PASS"]:
        return "BACKTEST_BLOCKED"

    # Full validation first.
    if not gates["OVERFIT_PASS"]:
        return "PENDING_OVERFIT"

    if not gates["LOGIC_PASS"]:
        return "PENDING_LOGIC"

    if not gates[
        "POLICY_VALIDATION_PASS"
    ]:
        return "PENDING_POLICY_VALIDATION"

    # Only after full validation comes the mandatory
    # minimal runtime Shadow safety gate.
    if not gates["SHADOW_PASS"]:
        return "PENDING_SHADOW"

    return "LOCAL_VALIDATION_COMPLETE"



def main():

    cv = governance.validate_constitution()

    if not cv.get("ok"):
        raise RuntimeError(
            "ROADMAP_CONSTITUTION_INVALID"
        )

    if (
        not ORDER.is_file()
        or sha256(ORDER)
        != EXPECTED_ORDER_HASH
    ):
        raise RuntimeError(
            "ORDER_FROZEN_HASH_MISMATCH"
        )

    found = candidate_result_files()

    # ========================================================
    # ?? ?? ?? Strategy Version? ??? ??? ????.
    #
    # ????? ??? ?? ???? ? ?? Trial? ???
    # RESEARCH_CANDIDATE/VALIDATED/CANARY/SMALL ???
    # ?? ??? ??? ???.
    # ========================================================

    reg = governance.version_registry()

    pending = []

    for version_id, version_row in (
        reg.get("strategy_versions")
        or {}
    ).items():

        if not isinstance(
            version_row,
            dict,
        ):
            continue

        status = str(
            version_row.get("status")
            or ""
        ).upper()

        if status not in {
            "RESEARCH_CANDIDATE",
            "VALIDATED",
            "PROVISIONAL",
            "CANARY",
            "SMALL",
        }:
            continue

        pending.append(
            (
                str(
                    version_row.get(
                        "created_at"
                    )
                    or ""
                ),
                str(version_id),
                version_row,
            )
        )

    pending.sort(
        key=lambda x: x[0],
        reverse=True,
    )

    pinned = None

    if pending:

        _, pinned_version_id, pinned = (
            pending[0]
        )

        pp = (
            pinned.get("payload")
            or {}
        )

        pinned_strategy = str(
            pp.get("strategy")
            or ""
        ).upper()

        pinned_tf = str(
            pp.get("timeframe")
            or ""
        ).upper()

        matched = []

        for item in found:

            _, result_path, row = item

            if (
                str(
                    row.get("strategy")
                    or ""
                ).upper()
                == pinned_strategy
                and
                str(
                    row.get("timeframe")
                    or ""
                ).upper()
                == pinned_tf
            ):
                matched.append(
                    item
                )

        if not matched:
            raise RuntimeError(
                "PINNED_CANDIDATE_EVIDENCE_MISSING="
                + pinned_version_id
            )

        _, result_path, row = matched[0]

        candidate_pin = "EXISTING"

    else:

        if not found:

            out = {
                "schema":
                    "B_BOT_V3_LOCAL_RELEASE_VALIDATION_V1",

                "updated_at":
                    now(),

                "candidate":
                    None,

                "decision":
                    "NO_RESEARCH_CANDIDATE",

                "server_write":
                    False,

                "real_order":
                    False,
            }

            atomic(
                STATE,
                out,
            )

            print(
                "LOCAL_VALIDATION="
                "NO_RESEARCH_CANDIDATE"
            )

            return 0

        _, result_path, row = found[0]

        pinned = None
        pinned_version_id = ""
        candidate_pin = "NEW"

    strategy = str(
        row.get("strategy")
        or ""
    ).upper()

    timeframe = str(
        row.get("timeframe")
        or ""
    ).upper()

    source = source_file_for(
        row
    )

    backtest_ok = valid_backtest(
        row
    )

    if source is None:
        backtest_ok = False

    if pinned is None:

        payload = {
            "timeframe":
                timeframe,

            "strategy":
                strategy,

            "source_file":
                (
                    source.relative_to(ROOT)
                    .as_posix()
                    if source is not None
                    else ""
                ),

            "research_strategy_version":
                row.get(
                    "strategy_version"
                ),

            "research_result":
                result_path
                .relative_to(ROOT)
                .as_posix(),

            "research_result_sha256":
                sha256(result_path),
        }

        initial_evidence = {
            "source":
                "LOCAL_CONTINUOUS_RESEARCH",

            "registered_at":
                now(),

            "backtest_pass":
                bool(backtest_ok),

            "confidence_level":
                row.get(
                    "confidence_level"
                ),

            "evaluation":
                row.get(
                    "evaluation"
                ),

            "trial":
                row.get("trial"),

            "real_order":
                False,

            "server_mutation":
                False,
        }

        version = (
            governance
            .register_version_candidate(
                "STRATEGY",
                "STRATEGY",
                payload,
                initial_evidence,
            )
        )

    else:

        version = pinned
        payload = (
            version.get("payload")
            or {}
        )

    version_id = str(
        version["version_id"]
    )

    # ========================================================
    # R4 검증 -> LOCAL_TEST -> LOCAL_SHADOW -> CANARY 승인
    # ========================================================
    gates = {
        "BACKTEST_PASS":
            bool(backtest_ok),

        "OVERFIT_PASS":
            evidence_pass(
                version_id,
                "overfit",
            ),

        "LOGIC_PASS":
            evidence_pass(
                version_id,
                "logic",
            ),

        "POLICY_VALIDATION_PASS":
            evidence_pass(
                version_id,
                "policy_validation",
            ),
    }

    pre_shadow_pass = all(
        gates.values()
    )

    current_status = str(
        version.get("status")
        or ""
    ).upper()

    if (
        pre_shadow_pass
        and current_status
        == "RESEARCH_CANDIDATE"
    ):
        version = (
            governance
            .set_version_status(
                version_id,
                "VALIDATED",
                {
                    "reason":
                        "LOCAL_PRE_SHADOW_VALIDATION_PASS",

                    "validation_gates":
                        gates,

                    "promotion_next":
                        "LOCAL_TEST_THEN_LOCAL_SHADOW",

                    "at":
                        now(),
                },
            )
        )

        current_status = str(
            version.get("status")
            or ""
        ).upper()

    strategy_instance_id = ""
    policy_bundle_version = ""
    strategy_instance_status = ""

    if (
        pre_shadow_pass
        and current_status
        in {
            "VALIDATED",
            "PROVISIONAL",
            "CANARY",
            "SMALL",
            "NORMAL",
        }
    ):
        runtime_cfg = load(
            STRATEGY_CONFIG,
            {},
        )

        if not isinstance(runtime_cfg, dict):
            raise RuntimeError(
                "STRATEGY_CONFIG_INVALID"
            )

        behavior_policy = {
            "trailing":
                runtime_cfg.get("trailing") or {},

            "state_reentry":
                runtime_cfg.get("state_reentry") or {},

            "opposite_exit":
                runtime_cfg.get("opposite_exit") or {},

            "opposite_reopen":
                runtime_cfg.get("opposite_reopen") or {},
        }

        policy_bundle = (
            governance
            .strategy_policy_bundle(
                runtime_cfg.get("policy_versions") or {},
                behavior_policy,
            )
        )

        policy_bundle_version = str(
            policy_bundle.get("version")
            or ""
        )

        instance = (
            governance
            .register_strategy_instance(
                strategy,
                timeframe,
                version_id,
                policy_bundle_version,
                policy_bundle.get("snapshot") or {},
                source_file=str(
                    payload.get("source_file")
                    or ""
                ),
                evidence={
                    "source":
                        "LOCAL_RELEASE_APPROVAL",

                    "validation_gates":
                        gates,

                    "at":
                        now(),
                },
                status="LOCAL_TEST",
            )
        )

        strategy_instance_id = str(
            instance.get("strategy_instance_id")
            or ""
        )

        strategy_instance_status = str(
            instance.get("status")
            or ""
        ).upper()

        if strategy_instance_status == "LOCAL_TEST":
            instance = (
                governance
                .set_strategy_instance_status(
                    strategy_instance_id,
                    "LOCAL_SHADOW",
                    {
                        "reason":
                            "LOCAL_TEST_VALIDATION_PASS",

                        "validation_gates":
                            gates,

                        "at":
                            now(),
                    },
                )
            )

            strategy_instance_status = str(
                instance.get("status")
                or ""
            ).upper()

    gates["SHADOW_PASS"] = (
        bool(strategy_instance_id)
        and minimal_shadow_pass(
            version_id,
            strategy_instance_id,
        )
    )

    if (
        gates["SHADOW_PASS"]
        and strategy_instance_status
        == "LOCAL_SHADOW"
    ):
        instance = (
            governance
            .set_strategy_instance_status(
                strategy_instance_id,
                "CANARY",
                {
                    "reason":
                        "MINIMAL_SHADOW_RUNTIME_GATE_PASS",

                    "shadow_evidence":
                        str(
                            evidence_file(
                                version_id,
                                "shadow",
                                strategy_instance_id,
                            )
                            .relative_to(ROOT)
                        ),

                    "at":
                        now(),
                },
            )
        )

        strategy_instance_status = str(
            instance.get("status")
            or ""
        ).upper()

    decision = status_for_gates(
        gates
    )

    all_pass = all(
        gates.values()
    )

    approved_for_server_release = (
        all_pass
        and strategy_instance_status
        == "CANARY"
    )

    out = {
        "schema":
            "B_BOT_V3_LOCAL_RELEASE_VALIDATION_V1",

        "updated_at":
            now(),

        "candidate_pin":
            candidate_pin,

        "candidate": {
            "version_id":
                version_id,

            "strategy_instance_id":
                strategy_instance_id,

            "strategy_policy_bundle_version":
                policy_bundle_version,

            "strategy_instance_status":
                strategy_instance_status,

            "strategy":
                strategy,

            "timeframe":
                timeframe,

            "source_file":
                payload.get(
                    "source_file"
                ),

            "research_result":
                payload.get(
                    "research_result"
                ),

            "confidence_level":
                row.get(
                    "confidence_level"
                ),

            "trial":
                row.get(
                    "trial"
                ),
        },

        "version_status":
            current_status,

        "validation_gates":
            gates,

        "all_local_validation_pass":
            all_pass,

        "decision":
            decision,

        "normal_approved":
            current_status == "NORMAL",

        "approved_for_server_release":
            approved_for_server_release,

        "server_push_hold":
            True,

        "server_write":
            False,

        "real_order":
            False,
    }

    atomic(
        STATE,
        out,
    )

    if (
        APPROVAL.exists()
        and not approved_for_server_release
    ):
        APPROVAL.unlink()

    print(
        "CANDIDATE_PIN="
        + candidate_pin
    )

    print(
        "VERSION_ID="
        + version_id
    )

    print(
        "STRATEGY="
        + strategy
    )

    print(
        "TIMEFRAME="
        + timeframe
    )

    print(
        "TRIAL="
        + str(
            row.get("trial")
        )
    )

    print(
        "VERSION_STATUS="
        + current_status
    )

    for key, value in gates.items():

        print(
            f"{key}="
            + (
                "PASS"
                if value
                else "WAIT"
            )
        )

    print(
        "LOCAL_VALIDATION_DECISION="
        + decision
    )

    print(
        "APPROVED_FOR_SERVER_RELEASE="
        + (
            "YES"
            if approved_for_server_release
            else "NO"
        )
    )

    print(
        "SERVER_WRITE=0"
    )

    print(
        "REAL_ORDER=0"
    )

    print(
        "SERVER_PUSH_HOLD=ON"
    )

    return 0



if __name__ == "__main__":
    raise SystemExit(main())
