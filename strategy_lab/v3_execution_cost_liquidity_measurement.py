from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


ROOT = Path(os.getenv("BBOT_LOCAL_ROOT", r"C:\my\bottest"))

TELEMETRY = (
    ROOT
    / "data"
    / "server_runtime"
    / "state"
    / "green7a_execution_telemetry_evidence.json"
)

CANDLES = ROOT / "data" / "candles"

OUT = (
    ROOT
    / "strategy_lab"
    / "results"
    / "v3"
    / "execution_cost_liquidity_measurement.json"
)

SCHEMA = "B_BOT_V3_EXECUTION_COST_LIQUIDITY_MEASUREMENT_V1"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load(path: Path, default):
    try:
        return json.loads(
            Path(path).read_text(encoding="utf-8-sig")
        )
    except Exception:
        return default


def atomic(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(
            obj,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def finite(value):
    try:
        x = float(value)
    except Exception:
        return None
    return x if math.isfinite(x) else None


def canonical_sha(obj) -> str:
    raw = json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def run_once() -> dict:
    source = load(TELEMETRY, {})
    records = source.get("records") or []

    observations = []
    rejected = []

    for index, outer in enumerate(records):
        telemetry = outer.get("telemetry") or {}
        execution = telemetry.get("execution_evidence") or {}
        summary = execution.get("summary") or {}
        preorder = telemetry.get("preorder_reference") or {}
        reference_record = preorder.get("record") or {}

        symbol = str(
            reference_record.get("symbol")
            or summary.get("symbol")
            or ""
        ).upper()

        order_id = str(summary.get("order_id") or "")

        capture_ns = int(
            reference_record.get("captured_at_ns") or 0
        )
        capture_ms = capture_ns // 1_000_000

        trade_times = [
            int(x)
            for x in (summary.get("trade_times") or [])
            if x is not None
        ]

        first_fill_ms = (
            min(trade_times)
            if trade_times
            else 0
        )

        timing = str(
            reference_record.get("reference_timing") or ""
        )

        fee_bps = finite(
            execution.get("actual_fee_bps")
        )

        abs_slippage_bps = finite(
            execution.get("actual_abs_slippage_bps")
        )

        slippage_cost_bps = finite(
            execution.get("actual_slippage_cost_bps")
        )

        quote_qty = finite(
            summary.get("actual_quote_qty")
        )

        reason = None

        if not symbol or not order_id:
            reason = "IDENTITY_MISSING"

        elif capture_ms <= 0 or first_fill_ms <= 0:
            reason = "TIME_BINDING_MISSING"

        elif capture_ms >= first_fill_ms:
            reason = "PREORDER_NOT_BEFORE_FILL"

        elif (
            timing
            != "AFTER_FINAL_ENTRY_RISK_BEFORE_MARKET_ORDER"
        ):
            reason = "PREORDER_TIMING_CONTRACT_INVALID"

        elif fee_bps is None or abs_slippage_bps is None:
            reason = "ACTUAL_COST_OBSERVATION_MISSING"

        candle_path = CANDLES / symbol / "1h.parquet"

        if reason is None and not candle_path.is_file():
            reason = "PIT_CANDLE_FILE_MISSING"

        if reason is not None:
            rejected.append(
                {
                    "record_index": index,
                    "symbol": symbol,
                    "order_id": order_id,
                    "reason": reason,
                }
            )
            continue

        try:
            df = pd.read_parquet(
                candle_path,
                columns=[
                    "open_time",
                    "close_time",
                    "quote_volume",
                    "trade_count",
                    "taker_buy_base_volume",
                    "taker_buy_quote_volume",
                ],
            )

            close_ms = pd.to_numeric(
                df["close_time"],
                errors="coerce",
            )

            prior = df.loc[close_ms < capture_ms].copy()

            if prior.empty:
                raise RuntimeError(
                    "NO_CLOSED_PIT_CANDLE_BEFORE_PREORDER"
                )

            prior["_close_ms"] = pd.to_numeric(
                prior["close_time"],
                errors="coerce",
            )

            row = prior.sort_values(
                "_close_ms",
                kind="mergesort",
            ).iloc[-1]

            quote_volume = finite(row["quote_volume"])
            trade_count = finite(row["trade_count"])
            taker_buy_base = finite(
                row["taker_buy_base_volume"]
            )
            taker_buy_quote = finite(
                row["taker_buy_quote_volume"]
            )

            if (
                quote_volume is None
                or quote_volume <= 0
                or trade_count is None
                or taker_buy_base is None
                or taker_buy_quote is None
            ):
                raise RuntimeError(
                    "PIT_LIQUIDITY_FIELDS_INCOMPLETE"
                )

            pit_row = {
                "open_time": str(row["open_time"]),
                "close_time": finite(row["close_time"]),
                "quote_volume": quote_volume,
                "trade_count": trade_count,
                "taker_buy_base_volume": taker_buy_base,
                "taker_buy_quote_volume": taker_buy_quote,
            }

            participation = (
                quote_qty / quote_volume
                if quote_qty is not None
                and quote_volume > 0
                else None
            )

            observations.append(
                {
                    "record_index": index,
                    "symbol": symbol,
                    "order_id": order_id,
                    "strategy_version": str(
                        reference_record.get(
                            "strategy_version"
                        )
                        or ""
                    ),
                    "timeframe": str(
                        reference_record.get("entry_tf")
                        or outer.get("timeframe")
                        or ""
                    ).upper(),
                    "captured_at_ns": capture_ns,
                    "first_fill_ms": first_fill_ms,
                    "preorder_to_fill_ms":
                        first_fill_ms - capture_ms,
                    "actual_fee_bps": fee_bps,
                    "actual_abs_slippage_bps":
                        abs_slippage_bps,
                    "actual_slippage_cost_bps":
                        slippage_cost_bps,
                    "actual_quote_qty": quote_qty,
                    "pit_1h": pit_row,
                    "pit_row_sha256":
                        canonical_sha(pit_row),
                    "order_quote_to_pit_quote_volume":
                        participation,
                }
            )

        except Exception as exc:
            rejected.append(
                {
                    "record_index": index,
                    "symbol": symbol,
                    "order_id": order_id,
                    "reason":
                        f"PIT_BINDING_FAILED:{type(exc).__name__}:{exc}",
                }
            )

    artifact = {
        "schema": SCHEMA,
        "generated_at": now(),
        "status": "MEASURED",
        "validated": False,
        "independent_validation": False,
        "policy_candidate_allowed": False,
        "live_effect": False,
        "actual_execution_telemetry_role":
            "REFERENCE_ONLY_NON_BLOCKING_FOR_ALL_PROMOTIONS",
        "numeric_thresholds": None,
        "threshold_derivation_performed": False,
        "threshold_source": None,
        "measurement_contract":
            "EXACT_PREORDER_BEFORE_FILL_PLUS_LAST_CLOSED_1H_PIT_LIQUIDITY",
        "telemetry_record_count": len(records),
        "usable_observation_count": len(observations),
        "rejected_observation_count": len(rejected),
        "observations": observations,
        "rejected": rejected,
        "next_required_stage":
            "INDEPENDENT_COST_LIQUIDITY_VALIDATION_DESIGN",
    }

    atomic(OUT, artifact)
    return artifact


if __name__ == "__main__":
    result = run_once()

    print(
        "SCHEMA="
        + str(result.get("schema"))
    )
    print(
        "STATUS="
        + str(result.get("status"))
    )
    print(
        "USABLE_OBSERVATIONS="
        + str(result.get("usable_observation_count"))
    )
    print(
        "REJECTED="
        + str(result.get("rejected_observation_count"))
    )
    print(
        "VALIDATED="
        + str(result.get("validated"))
    )
    print(
        "POLICY_CANDIDATE_ALLOWED="
        + str(result.get("policy_candidate_allowed"))
    )
    print(
        "THRESHOLD_DERIVED="
        + str(result.get("threshold_derivation_performed"))
    )
