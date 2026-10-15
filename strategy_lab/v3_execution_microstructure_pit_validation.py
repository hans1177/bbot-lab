# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(
    os.getenv(
        "BBOT_LOCAL_ROOT",
        r"C:\my\bottest",
    )
)

MICRO = (
    ROOT
    / "data"
    / "server_runtime"
    / "state"
    / "green7a_preorder_microstructure_evidence.json"
)

TELEMETRY = (
    ROOT
    / "data"
    / "server_runtime"
    / "state"
    / "green7a_execution_telemetry_evidence.json"
)

OUT = (
    ROOT
    / "strategy_lab"
    / "results"
    / "v3"
    / "execution_microstructure_pit_evidence_validation.json"
)

SCHEMA = (
    "B_BOT_V3_EXECUTION_MICROSTRUCTURE_PIT_EVIDENCE_VALIDATION_V1"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def load(path: Path) -> Any:
    return json.loads(
        path.read_text(
            encoding="utf-8-sig"
        )
    )


def atomic(path: Path, obj: Any) -> None:
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
        ) + "\n",
        encoding="utf-8",
    )

    os.replace(
        tmp,
        path,
    )


def exact_fill_index() -> dict[str, dict[str, Any]]:
    obj = load(
        TELEMETRY
    )

    records = obj.get(
        "records"
    )

    if not isinstance(
        records,
        list,
    ):
        raise RuntimeError(
            "TELEMETRY_RECORDS_INVALID"
        )

    out = {}

    for row in records:
        if not isinstance(
            row,
            dict,
        ):
            continue

        entry_id = str(
            row.get("entry_id")
            or ""
        ).strip()

        order_id = str(
            row.get("order_id")
            or ""
        ).strip()

        telemetry = (
            row.get("telemetry")
            or {}
        )

        execution = (
            telemetry.get(
                "execution_evidence"
            )
            or {}
        )

        summary = (
            execution.get(
                "summary"
            )
            or {}
        )

        summary_order_id = str(
            summary.get("order_id")
            or ""
        ).strip()

        if (
            not entry_id
            or not order_id
            or summary_order_id
            != order_id
        ):
            continue

        trade_times = (
            summary.get(
                "trade_times"
            )
        )

        if not isinstance(
            trade_times,
            list,
        ):
            continue

        times = []

        for value in trade_times:
            try:
                times.append(
                    int(value)
                )
            except Exception:
                pass

        if not times:
            continue

        if entry_id in out:
            raise RuntimeError(
                "DUPLICATE_ENTRY_ID_TELEMETRY="
                + entry_id
            )

        out[entry_id] = {
            "entry_id":
                entry_id,

            "order_id":
                order_id,

            "symbol":
                str(
                    row.get("symbol")
                    or ""
                ).upper(),

            "strategy_version":
                str(
                    row.get(
                        "strategy_version"
                    )
                    or ""
                ),

            "timeframe":
                str(
                    row.get("timeframe")
                    or ""
                ).upper(),

            "first_fill_ms":
                min(times),
        }

    return out


def validate_one(
    entry_id: str,
    micro: dict[str, Any],
    fill: dict[str, Any],
) -> tuple[bool, list[str], dict[str, Any]]:

    reasons = []

    if (
        str(
            micro.get("entry_id")
            or ""
        )
        != entry_id
    ):
        reasons.append(
            "ENTRY_ID_BINDING_MISMATCH"
        )

    symbol = str(
        micro.get("symbol")
        or ""
    ).upper()

    if (
        symbol
        != fill.get("symbol")
    ):
        reasons.append(
            "SYMBOL_BINDING_MISMATCH"
        )

    if (
        str(
            micro.get(
                "strategy_version"
            )
            or ""
        )
        != fill.get(
            "strategy_version"
        )
    ):
        reasons.append(
            "STRATEGY_VERSION_BINDING_MISMATCH"
        )

    if (
        str(
            micro.get("timeframe")
            or ""
        ).upper()
        != fill.get("timeframe")
    ):
        reasons.append(
            "TIMEFRAME_BINDING_MISMATCH"
        )

    if (
        micro.get(
            "reference_timing"
        )
        !=
        "AFTER_FINAL_ENTRY_RISK_BEFORE_MARKET_ORDER"
    ):
        reasons.append(
            "REFERENCE_TIMING_INVALID"
        )

    if (
        micro.get(
            "order_api_used"
        )
        is not False
    ):
        reasons.append(
            "ORDER_API_CONTRACT_INVALID"
        )

    if (
        micro.get(
            "real_order_created"
        )
        is not False
    ):
        reasons.append(
            "REAL_ORDER_CONTRACT_INVALID"
        )

    try:
        preorder_ns = int(
            micro.get(
                "preorder_captured_at_ns"
            )
            or 0
        )

        request_ns = int(
            micro.get(
                "micro_request_start_ns"
            )
            or 0
        )

        depth_receive_ns = int(
            micro.get(
                "depth_receive_ns"
            )
            or 0
        )

        first_fill_ms = int(
            fill.get(
                "first_fill_ms"
            )
            or 0
        )

    except Exception:
        preorder_ns = 0
        request_ns = 0
        depth_receive_ns = 0
        first_fill_ms = 0

    if (
        preorder_ns <= 0
        or request_ns <= 0
        or depth_receive_ns <= 0
        or first_fill_ms <= 0
    ):
        reasons.append(
            "TIMESTAMP_BINDING_MISSING"
        )

    else:
        if request_ns < preorder_ns:
            reasons.append(
                "MICRO_REQUEST_BEFORE_PREORDER_CAPTURE"
            )

        depth_receive_ms = (
            depth_receive_ns
            // 1_000_000
        )

        if (
            depth_receive_ms
            >= first_fill_ms
        ):
            reasons.append(
                "MICROSTRUCTURE_NOT_STRICTLY_PREFILL"
            )

    try:
        depth_t_ms = int(
            micro.get(
                "depth_transaction_time_ms"
            )
            or 0
        )
    except Exception:
        depth_t_ms = 0

    if depth_t_ms <= 0:
        reasons.append(
            "DEPTH_EXCHANGE_TIME_MISSING"
        )

    elif (
        first_fill_ms > 0
        and depth_t_ms
        >= first_fill_ms
    ):
        reasons.append(
            "DEPTH_EXCHANGE_TIME_NOT_PREFILL"
        )

    execution = (
        micro.get(
            "execution_from_depth"
        )
    )

    if not isinstance(
        execution,
        dict,
    ):
        reasons.append(
            "DEPTH_EXECUTION_RESULT_MISSING"
        )

    else:
        if (
            execution.get(
                "fully_executable"
            )
            is not True
        ):
            reasons.append(
                "ORDER_SIZE_NOT_FULLY_EXECUTABLE_IN_CAPTURED_DEPTH"
            )

        impact = execution.get(
            "abs_impact_bps_vs_preorder_reference"
        )

        try:
            impact = float(
                impact
            )
        except Exception:
            impact = None

        if (
            impact is None
            or impact < 0
        ):
            reasons.append(
                "DEPTH_IMPACT_INVALID"
            )

    accepted = (
        len(reasons) == 0
    )

    detail = {
        "entry_id":
            entry_id,

        "order_id":
            fill.get(
                "order_id"
            ),

        "symbol":
            symbol,

        "first_fill_ms":
            first_fill_ms,

        "depth_receive_ms":
            (
                depth_receive_ns
                // 1_000_000
                if depth_receive_ns > 0
                else None
            ),

        "depth_transaction_time_ms":
            depth_t_ms
            if depth_t_ms > 0
            else None,

        "accepted":
            accepted,

        "reasons":
            reasons,
    }

    return (
        accepted,
        reasons,
        detail,
    )


def run_once() -> dict[str, Any]:
    if not TELEMETRY.is_file():
        raise RuntimeError(
            "TELEMETRY_EVIDENCE_MISSING"
        )

    if not MICRO.is_file():
        result = {
            "schema":
                SCHEMA,

            "status":
                "WAITING_FOR_FORWARD_PIT_EVIDENCE",

            "validated":
                False,

            "independent_validation":
                False,

            "policy_candidate_allowed":
                False,

            "numeric_thresholds":
                None,

            "threshold_derivation_performed":
                False,

            "historical_backfill_allowed":
                False,

            "accepted_count":
                0,

            "rejected_count":
                0,

            "accepted":
                [],

            "rejected":
                [],

            "reason":
                "FORWARD_MICROSTRUCTURE_EVIDENCE_NOT_AVAILABLE",
        }

        atomic(
            OUT,
            result,
        )

        return result

    micro_obj = load(
        MICRO
    )

    records = micro_obj.get(
        "records"
    )

    if not isinstance(
        records,
        dict,
    ):
        raise RuntimeError(
            "MICROSTRUCTURE_RECORDS_INVALID"
        )

    fills = exact_fill_index()

    accepted = []
    rejected = []

    for entry_id, row in records.items():
        if not isinstance(
            row,
            dict,
        ):
            rejected.append({
                "entry_id":
                    str(entry_id),

                "reasons": [
                    "MICROSTRUCTURE_RECORD_INVALID"
                ],
            })
            continue

        fill = fills.get(
            str(entry_id)
        )

        if not isinstance(
            fill,
            dict,
        ):
            rejected.append({
                "entry_id":
                    str(entry_id),

                "reasons": [
                    "EXACT_FILL_NOT_AVAILABLE_YET"
                ],
            })
            continue

        ok, reasons, detail = (
            validate_one(
                str(entry_id),
                row,
                fill,
            )
        )

        if ok:
            accepted.append(
                detail
            )
        else:
            rejected.append(
                detail
            )

    # This validator validates PIT evidence structure only.
    # It never derives a numeric policy threshold.
    status = (
        "PIT_EVIDENCE_AVAILABLE"
        if accepted
        else
        "WAITING_FOR_VALID_PIT_EVIDENCE"
    )

    result = {
        "schema":
            SCHEMA,

        "status":
            status,

        "validated":
            False,

        "independent_validation":
            False,

        "policy_candidate_allowed":
            False,

        "numeric_thresholds":
            None,

        "threshold_derivation_performed":
            False,

        "historical_backfill_allowed":
            False,

        "microstructure_source_sha256":
            sha256(MICRO),

        "telemetry_source_sha256":
            sha256(TELEMETRY),

        "accepted_count":
            len(accepted),

        "rejected_count":
            len(rejected),

        "accepted":
            accepted,

        "rejected":
            rejected,

        "next_required_stage":
            "INDEPENDENT_NUMERIC_SLIPPAGE_POLICY_VALIDATION",
    }

    atomic(
        OUT,
        result,
    )

    return result


if __name__ == "__main__":
    r = run_once()

    print(
        "STATUS="
        +str(r.get("status"))
    )

    print(
        "ACCEPTED_COUNT="
        +str(r.get("accepted_count"))
    )

    print(
        "REJECTED_COUNT="
        +str(r.get("rejected_count"))
    )

    print(
        "VALIDATED="
        +str(r.get("validated"))
    )

    print(
        "POLICY_CANDIDATE_ALLOWED="
        +str(
            r.get(
                "policy_candidate_allowed"
            )
        )
    )

    print(
        "NUMERIC_THRESHOLDS="
        +repr(
            r.get(
                "numeric_thresholds"
            )
        )
    )

    print(
        "THRESHOLD_DERIVATION_PERFORMED="
        +str(
            r.get(
                "threshold_derivation_performed"
            )
        )
    )