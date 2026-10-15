from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import json
import math
import os
import sqlite3
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
V3 = ROOT / "strategy_lab" / "results" / "v3"

DB = (
    ROOT
    / "data"
    / "server_runtime"
    / "history.db"
)

PREDICTIONS = (
    V3
    / "candidate_entry_risk_forward_shadow_evidence.jsonl"
)

OUTCOMES = (
    V3
    / "candidate_entry_risk_forward_outcomes.jsonl"
)

SCHEMA = (
    "B_BOT_V3_CANDIDATE_ENTRY_RISK_FORWARD_OUTCOME_V1"
)


def _text(value: Any) -> str:
    return str(
        value
        or ""
    ).strip()


def _finite(value: Any):
    try:
        x = float(value)
    except Exception:
        return None

    if not math.isfinite(x):
        return None

    return x


def _load_jsonl(path: Path):
    rows = []

    if not path.is_file():
        return rows

    for line in path.read_text(
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
                rows.append(
                    obj
                )

        except Exception:
            continue

    return rows


def _write_jsonl(
    path: Path,
    rows,
):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp = path.with_suffix(
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
        path,
    )


def _stop_label(
    exit_type,
    reason,
):
    et = _text(
        exit_type
    ).upper()

    rs = _text(
        reason
    ).upper()

    label_text = (
        et
        + " "
        + rs
    ).strip()

    if not label_text:
        return (
            None,
            "EXIT_LABEL_UNAVAILABLE",
        )

    stopish = (
        "ABS_STOP"
        in label_text
        or
        "CONTROL_SL"
        in label_text
        or
        "STOP_LOSS"
        in label_text
        or
        "STOP LOSS"
        in label_text
        or
        "STOP"
        in label_text
        or
        et == "SL"
    )

    return (
        1.0
        if stopish
        else 0.0,
        "OBSERVED_EXIT_LABEL",
    )


def run_once():
    """Collect outcomes only for predictions recorded before outcome existed."""

    predictions = _load_jsonl(
        PREDICTIONS
    )

    existing = _load_jsonl(
        OUTCOMES
    )

    existing_ids = {
        _text(
            row.get("entry_id")
        )
        for row in existing
        if _text(
            row.get("entry_id")
        )
    }

    if not predictions:
        return {
            "status":
                "NO_FORWARD_PREDICTIONS_YET",

            "prediction_rows":
                0,

            "new_outcomes":
                0,

            "total_outcomes":
                len(existing),

            "live_effect":
                False,

            "policy_effect":
                "NONE",
        }

    if not DB.is_file():
        return {
            "status":
                "DB_MISSING",

            "prediction_rows":
                len(predictions),

            "new_outcomes":
                0,

            "total_outcomes":
                len(existing),

            "live_effect":
                False,

            "policy_effect":
                "NONE",
        }

    prediction_map = {}

    duplicate_prediction_ids = set()

    for row in predictions:
        entry_id = _text(
            row.get("entry_id")
        )

        if not entry_id:
            continue

        if entry_id in prediction_map:
            duplicate_prediction_ids.add(
                entry_id
            )

        prediction_map[
            entry_id
        ] = row

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
        cols = {
            str(row[1])
            for row in conn.execute(
                "PRAGMA table_info(exit_history)"
            ).fetchall()
        }

        wanted = [
            name
            for name in (
                "id",
                "entry_id",
                "symbol",
                "exit_price",
                "ts",
                "exit_ts",
                "exit_type",
                "reason",
                "roi",
                "pnl",
                "source_module",
                "max_adverse_roi",
                "max_favorable_roi",
                "payload_json",
            )
            if name in cols
        ]

        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT "
                + ", ".join(wanted)
                + " FROM exit_history "
                + "WHERE entry_id IS NOT NULL "
                + "AND entry_id != '' "
                + "ORDER BY id ASC"
            ).fetchall()
        ]

    finally:
        conn.close()

    exit_by_entry = {}

    for row in rows:
        entry_id = _text(
            row.get("entry_id")
        )

        if entry_id not in prediction_map:
            continue

        exit_by_entry.setdefault(
            entry_id,
            [],
        ).append(
            row
        )

    new_rows = []

    skipped = {
        "prediction_duplicate":
            0,

        "already_collected":
            0,

        "no_exit_yet":
            0,

        "multiple_exit_rows":
            0,

        "invalid_exit_price":
            0,

        "missing_realized_mae":
            0,
    }

    for entry_id, prediction in prediction_map.items():

        if entry_id in existing_ids:
            skipped[
                "already_collected"
            ] += 1
            continue

        if entry_id in duplicate_prediction_ids:
            skipped[
                "prediction_duplicate"
            ] += 1
            continue

        matches = exit_by_entry.get(
            entry_id,
            [],
        )

        if not matches:
            skipped[
                "no_exit_yet"
            ] += 1
            continue

        # Fail closed on ambiguous lifecycle.
        if len(matches) != 1:
            skipped[
                "multiple_exit_rows"
            ] += 1
            continue

        exit_row = matches[0]

        exit_price = _finite(
            exit_row.get(
                "exit_price"
            )
        )

        realized_mae = _finite(
            exit_row.get(
                "max_adverse_roi"
            )
        )

        realized_mfe = _finite(
            exit_row.get(
                "max_favorable_roi"
            )
        )

        quality_reasons = []

        if (
            exit_price is None
            or exit_price <= 0
        ):
            quality_reasons.append(
                "INVALID_EXIT_PRICE"
            )

            skipped[
                "invalid_exit_price"
            ] += 1

        if realized_mae is None:
            quality_reasons.append(
                "MISSING_REALIZED_MAE"
            )

            skipped[
                "missing_realized_mae"
            ] += 1

        stop_label, stop_status = (
            _stop_label(
                exit_row.get(
                    "exit_type"
                ),
                exit_row.get(
                    "reason"
                ),
            )
        )

        # Zero MAE is allowed.  A real zero must not be silently rejected.
        quality_status = (
            "USABLE"
            if not quality_reasons
            else "REJECTED"
        )

        record = {
            "schema":
                SCHEMA,

            "evidence_class":
                "FORWARD_SHADOW_REALIZED_OUTCOME",

            "entry_id":
                entry_id,

            "symbol":
                _text(
                    prediction.get(
                        "symbol"
                    )
                ).upper(),

            "side":
                _text(
                    prediction.get(
                        "side"
                    )
                ).upper(),

            "timeframe":
                _text(
                    prediction.get(
                        "timeframe"
                    )
                ).upper(),

            "strategy":
                _text(
                    prediction.get(
                        "strategy"
                    )
                ),

            "prediction_recorded_at":
                prediction.get(
                    "recorded_at"
                ),

            "prediction_entry_epoch":
                prediction.get(
                    "entry_epoch"
                ),

            "predicted_expected_adverse_move":
                prediction.get(
                    "predicted_expected_adverse_move"
                ),

            "predicted_stop_hit_probability":
                prediction.get(
                    "predicted_stop_hit_probability"
                ),

            "exit_history_id":
                exit_row.get("id"),

            "exit_price":
                exit_price,

            "exit_ts":
                exit_row.get(
                    "exit_ts"
                )
                or
                exit_row.get("ts"),

            "exit_type":
                _text(
                    exit_row.get(
                        "exit_type"
                    )
                ),

            "reason":
                _text(
                    exit_row.get(
                        "reason"
                    )
                ),

            "roi":
                _finite(
                    exit_row.get("roi")
                ),

            "pnl":
                _finite(
                    exit_row.get("pnl")
                ),

            "source_module":
                _text(
                    exit_row.get(
                        "source_module"
                    )
                ),

            "realized_mae":
                (
                    abs(
                        realized_mae
                    )
                    if realized_mae
                    is not None
                    else None
                ),

            "realized_mfe":
                (
                    abs(
                        realized_mfe
                    )
                    if realized_mfe
                    is not None
                    else None
                ),

            "stop_label":
                stop_label,

            "stop_label_status":
                stop_status,

            "quality_status":
                quality_status,

            "quality_reasons":
                quality_reasons,

            "outcome_after_forward_prediction":
                True,

            "retrospective_only":
                False,

            "policy_candidate_allowed":
                False,

            "live_effect":
                False,

            "policy_effect":
                "NONE",

            "collected_at":
                datetime.now(
                    timezone.utc
                ).isoformat(),
        }

        new_rows.append(
            record
        )

        existing_ids.add(
            entry_id
        )

    if new_rows:
        _write_jsonl(
            OUTCOMES,
            existing + new_rows,
        )

    usable = sum(
        1
        for row in (
            existing + new_rows
        )
        if row.get(
            "quality_status"
        )
        == "USABLE"
    )

    return {
        "status":
            "FORWARD_OUTCOME_ACCUMULATING",

        "prediction_rows":
            len(predictions),

        "new_outcomes":
            len(new_rows),

        "total_outcomes":
            len(existing)
            + len(new_rows),

        "usable_outcomes":
            usable,

        "skipped":
            skipped,

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
