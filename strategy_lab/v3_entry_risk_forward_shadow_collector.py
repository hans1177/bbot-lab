from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import importlib.util
import json
import math
import os
import sqlite3
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "strategy_lab"
V3 = LAB / "results" / "v3"

DB = (
    ROOT
    / "data"
    / "server_runtime"
    / "history.db"
)

HISTORICAL_MODEL = (
    LAB
    / "v3_entry_risk_historical_pit_validation.py"
)

LEDGER = (
    V3
    / "candidate_entry_risk_forward_shadow_evidence.jsonl"
)

SCHEMA = (
    "B_BOT_V3_CANDIDATE_ENTRY_RISK_FORWARD_SHADOW_V1"
)

MODEL_VERSION = (
    "ER_EMPIRICAL_PIT_FORWARD_SHADOW_V1"
)

STOP_STATUS = (
    "UNAVAILABLE_SOURCE_HAS_NO_STOP_LABELS"
)


def _finite(value: Any):
    try:
        result = float(value)
    except Exception:
        return None

    if not math.isfinite(result):
        return None

    return result


def _text(value: Any) -> str:
    return str(
        value
        or ""
    ).strip()


def _load_module(path: Path, name: str):
    spec = (
        importlib.util
        .spec_from_file_location(
            name,
            path,
        )
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise RuntimeError(
            "MODULE_LOAD_FAILED="
            + str(path)
        )

    module = (
        importlib.util
        .module_from_spec(
            spec
        )
    )

    spec.loader.exec_module(
        module
    )

    return module


def _table_columns(
    conn,
    table: str,
):
    safe = (
        '"'
        + table.replace(
            '"',
            '""'
        )
        + '"'
    )

    return {
        str(row[1])
        for row in conn.execute(
            "PRAGMA table_info("
            + safe
            + ")"
        ).fetchall()
    }


def _load_existing():
    result = []

    if not LEDGER.is_file():
        return result

    for line in LEDGER.read_text(
        encoding="utf-8-sig",
        errors="replace",
    ).splitlines():

        if not line.strip():
            continue

        try:
            obj = json.loads(
                line
            )

            if isinstance(
                obj,
                dict,
            ):
                result.append(
                    obj
                )

        except Exception:
            continue

    return result


def _write_all(rows):
    LEDGER.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp = LEDGER.with_suffix(
        ".jsonl.tmp"
    )

    tmp.write_text(
        "".join(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
        newline="\n",
    )

    os.replace(
        tmp,
        LEDGER,
    )


def run_once():
    """Record outcome-blind Entry Risk predictions for real entry rows.

    The collector:
      1. reads entry_history from the runtime DB in SQLite mode=ro;
      2. rejects rows without minimum entry identity;
      3. rejects any entry_id already present in exit_history;
      4. predicts using only historical trades whose exit timestamp is
         strictly earlier than this entry timestamp;
      5. writes only to a separate research SHADOW ledger.

    It does not rank candidates, resize positions, change leverage,
    block entries, call the exchange, or modify runner/order.
    """

    if not DB.is_file():
        return {
            "status":
                "DB_MISSING",

            "new_records":
                0,

            "live_effect":
                False,
        }

    historical = _load_module(
        HISTORICAL_MODEL,
        "v3_entry_risk_forward_historical_model",
    )

    source, source_sha = (
        historical.load_source()
    )

    training = historical.load_rows(
        source
    )

    existing = _load_existing()

    existing_ids = {
        _text(
            row.get("entry_id")
        )
        for row in existing
        if _text(
            row.get("entry_id")
        )
    }

    uri = (
        DB.resolve().as_uri()
        + "?mode=ro"
    )

    conn = sqlite3.connect(
        uri,
        uri=True,
    )

    conn.row_factory = sqlite3.Row

    try:
        entry_cols = _table_columns(
            conn,
            "entry_history",
        )

        exit_cols = _table_columns(
            conn,
            "exit_history",
        )

        required = {
            "entry_id",
            "symbol",
            "entry_price",
            "entry_tf",
            "entry_strategy",
        }

        missing = sorted(
            required
            - entry_cols
        )

        if missing:
            return {
                "status":
                    "ENTRY_HISTORY_SCHEMA_INCOMPLETE",

                "missing":
                    missing,

                "new_records":
                    0,

                "live_effect":
                    False,
            }

        wanted = [
            name
            for name in (
                "id",
                "entry_id",
                "symbol",
                "side",
                "position_side",
                "entry_direction",
                "entry_price",
                "entry_ts",
                "first_entry_ts",
                "ts",
                "entry_tf",
                "entry_strategy",
                "signal_id",
                "entry_order_id",
                "entry_client_order_id",
                "source_module",
                "route",
                "entry_type",
            )
            if name in entry_cols
        ]

        entries = [
            dict(row)
            for row in conn.execute(
                "SELECT "
                + ", ".join(wanted)
                + " FROM entry_history "
                + "ORDER BY id ASC"
            ).fetchall()
        ]

        closed_ids = set()

        if "entry_id" in exit_cols:
            closed_ids = {
                _text(
                    row[0]
                )
                for row in conn.execute(
                    """
                    SELECT entry_id
                    FROM exit_history
                    WHERE entry_id IS NOT NULL
                      AND entry_id != ''
                    """
                ).fetchall()
            }

    finally:
        conn.close()

    new_rows = []

    skip = {
        "already_recorded":
            0,

        "already_closed":
            0,

        "identity_incomplete":
            0,

        "order_identity_missing":
            0,

        "prediction_unavailable":
            0,
    }

    now = datetime.now(
        timezone.utc
    )

    for row in entries:
        entry_id = _text(
            row.get("entry_id")
        )

        if not entry_id:
            skip[
                "identity_incomplete"
            ] += 1
            continue

        if entry_id in existing_ids:
            skip[
                "already_recorded"
            ] += 1
            continue

        # Critical forward boundary:
        # never create a prediction after its outcome is already known.
        if entry_id in closed_ids:
            skip[
                "already_closed"
            ] += 1
            continue

        symbol = _text(
            row.get("symbol")
        ).upper()

        side = historical.side_name(
            row.get("side")
            or row.get(
                "position_side"
            )
            or row.get(
                "entry_direction"
            )
        )

        timeframe = _text(
            row.get(
                "entry_tf"
            )
        ).upper()

        strategy = _text(
            row.get(
                "entry_strategy"
            )
        )

        entry_price = _finite(
            row.get(
                "entry_price"
            )
        )

        entry_ts_raw = (
            row.get(
                "entry_ts"
            )
            or
            row.get(
                "first_entry_ts"
            )
            or
            row.get("ts")
        )

        entry_epoch = historical.epoch(
            entry_ts_raw
        )

        if (
            not symbol
            or
            not side
            or
            not timeframe
            or
            not strategy
            or
            entry_price is None
            or
            entry_price <= 0
            or
            entry_epoch is None
        ):
            skip[
                "identity_incomplete"
            ] += 1
            continue

        order_id = _text(
            row.get(
                "entry_order_id"
            )
        )

        client_order_id = _text(
            row.get(
                "entry_client_order_id"
            )
        )

        # If order identity columns exist, at least one must prove this
        # came through the actual entry lifecycle.
        order_columns_present = (
            "entry_order_id"
            in entry_cols
            or
            "entry_client_order_id"
            in entry_cols
        )

        if (
            order_columns_present
            and
            not order_id
            and
            not client_order_id
        ):
            skip[
                "order_identity_missing"
            ] += 1
            continue

        target = {
            "symbol":
                symbol,

            "side":
                side,

            "timeframe":
                timeframe,

            "entry_epoch":
                entry_epoch,
        }

        pred = historical.prediction(
            target,
            training,
        )

        if pred is None:
            skip[
                "prediction_unavailable"
            ] += 1
            continue

        predicted_mae = _finite(
            pred.get(
                "pred_mae"
            )
        )

        if predicted_mae is None:
            skip[
                "prediction_unavailable"
            ] += 1
            continue

        recorded_at = datetime.now(
            timezone.utc
        )

        record = {
            "schema":
                SCHEMA,

            "evidence_class":
                "FORWARD_SHADOW_ENTRY_TIME_PREDICTION",

            "effect_mode":
                "SHADOW",

            "model_version":
                MODEL_VERSION,

            "historical_model_source_sha256":
                source_sha,

            "entry_id":
                entry_id,

            "symbol":
                symbol,

            "side":
                side,

            "timeframe":
                timeframe,

            "strategy":
                strategy,

            "signal_id":
                _text(
                    row.get(
                        "signal_id"
                    )
                ),

            "entry_price":
                entry_price,

            "entry_ts":
                entry_ts_raw,

            "entry_epoch":
                entry_epoch,

            "entry_order_id":
                order_id,

            "entry_client_order_id":
                client_order_id,

            "source_module":
                _text(
                    row.get(
                        "source_module"
                    )
                ),

            "route":
                _text(
                    row.get(
                        "route"
                    )
                ),

            "entry_type":
                _text(
                    row.get(
                        "entry_type"
                    )
                ),

            "training_scope":
                pred.get(
                    "scope"
                ),

            "training_samples":
                int(
                    pred.get(
                        "training_samples"
                    )
                    or 0
                ),

            "predicted_expected_adverse_move":
                predicted_mae,

            "predicted_stop_hit_probability":
                None,

            "stop_prediction_status":
                STOP_STATUS,

            "predicted_safe_size_qty":
                None,

            "predicted_safe_leverage":
                None,

            "outcome_known_at_prediction":
                False,

            "forward_only":
                True,

            "retrospective_only":
                False,

            "independent_validation":
                False,

            "policy_candidate_allowed":
                False,

            "live_effect":
                False,

            "policy_effect":
                "NONE",

            "recorded_at":
                recorded_at.isoformat(),

            "prediction_delay_seconds":
                max(
                    0.0,
                    recorded_at.timestamp()
                    - entry_epoch,
                ),
        }

        new_rows.append(
            record
        )

        existing_ids.add(
            entry_id
        )

    if new_rows:
        _write_all(
            existing
            + new_rows
        )

    return {
        "status":
            "FORWARD_SHADOW_ACCUMULATING",

        "entry_history_rows":
            len(entries),

        "existing_forward_records":
            len(existing),

        "new_records":
            len(new_rows),

        "total_forward_records":
            len(existing)
            + len(new_rows),

        "skipped":
            skip,

        "stop_prediction_status":
            STOP_STATUS,

        "outcome_known_at_prediction":
            False,

        "policy_candidate_allowed":
            False,

        "live_effect":
            False,

        "policy_effect":
            "NONE",
    }


if __name__ == "__main__":
    print(
        json.dumps(
            run_once(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
