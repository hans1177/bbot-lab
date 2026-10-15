from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import argparse
import csv
import hashlib
import json
import math
import os
import sqlite3
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
V3 = ROOT / "strategy_lab" / "results" / "v3"

HISTORY_DB = (
    ROOT
    / "data"
    / "server_runtime"
    / "history.db"
)

PRIOR = (
    V3
    / "entry_risk_historical_prior.json"
)

OUT = (
    V3
    / "candidate_entry_risk_shadow_evidence.jsonl"
)

SCHEMA = (
    "B_BOT_V3_CANDIDATE_ENTRY_RISK_SHADOW_EVIDENCE_V1"
)

MODEL_VERSION = (
    "ER_SHADOW_EMPIRICAL_PIT_V1"
)

EVIDENCE_CLASS = (
    "RETROSPECTIVE_POINT_IN_TIME_SHADOW_REPLAY"
)

MIN_TRAIN_SAMPLES = 20
MIN_GLOBAL_TRAIN_SAMPLES = 50


def sha256(path: Path) -> str:
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def finite(value: Any) -> float | None:
    try:
        x = float(value)
    except Exception:
        return None

    if not math.isfinite(x):
        return None

    return x


def epoch(value: Any) -> float | None:
    if value is None:
        return None

    s = str(value).strip()

    if not s:
        return None

    try:
        x = float(s)

        if x > 1e17:
            x /= 1e9

        elif x > 1e14:
            x /= 1e6

        elif x > 1e11:
            x /= 1e3

        if 0 < x < 1e11:
            return x

    except Exception:
        pass

    try:
        dt = datetime.fromisoformat(
            s.replace(
                "Z",
                "+00:00",
            )
        )

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.timestamp()

    except Exception:
        return None


def iso_utc(value: float) -> str:
    return datetime.fromtimestamp(
        value,
        tz=timezone.utc,
    ).isoformat()


def side_name(value: Any) -> str:
    s = str(
        value
        or ""
    ).strip().upper()

    if s in {
        "LONG",
        "BUY",
        "1",
        "1.0",
    }:
        return "LONG"

    if s in {
        "SHORT",
        "SELL",
        "-1",
        "-1.0",
    }:
        return "SHORT"

    try:
        x = float(s)

        if x > 0:
            return "LONG"

        if x < 0:
            return "SHORT"

    except Exception:
        pass

    return s


def connection():
    if not HISTORY_DB.is_file():
        raise RuntimeError(
            "HISTORY_DB_MISSING"
        )

    uri = (
        HISTORY_DB.resolve().as_uri()
        + "?mode=ro"
    )

    con = sqlite3.connect(
        uri,
        uri=True,
    )

    con.row_factory = sqlite3.Row

    return con


def table_columns(
    table: str,
) -> set[str]:
    con = connection()

    try:
        rows = con.execute(
            "PRAGMA table_info("
            + table
            + ")"
        ).fetchall()

        return {
            str(row[1])
            for row in rows
        }

    finally:
        con.close()


def table_rows(
    table: str,
    columns: list[str],
) -> list[dict[str, Any]]:
    con = connection()

    try:
        rows = con.execute(
            "SELECT "
            + ", ".join(columns)
            + " FROM "
            + table
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]

    finally:
        con.close()


def entry_time_map():
    cols = table_columns(
        "entry_history"
    )

    if "entry_id" not in cols:
        return {}

    time_candidates = [
        c
        for c in (
            "entry_ts",
            "ts",
            "created_at",
            "utc",
            "entry_time",
            "timestamp",
            "signal_time",
            "signal_candle_time",
        )
        if c in cols
    ]

    if not time_candidates:
        return {}

    selected = [
        "entry_id",
    ] + time_candidates

    rows = table_rows(
        "entry_history",
        selected,
    )

    result = {}

    for row in rows:
        entry_id = str(
            row.get("entry_id")
            or ""
        ).strip()

        if not entry_id:
            continue

        times = [
            epoch(
                row.get(name)
            )
            for name in time_candidates
        ]

        times = [
            x
            for x in times
            if x is not None
        ]

        if not times:
            continue

        candidate = min(
            times
        )

        old = result.get(
            entry_id
        )

        if (
            old is None
            or candidate < old
        ):
            result[
                entry_id
            ] = candidate

    return result


def target_exits():
    cols = table_columns(
        "exit_history"
    )

    required = {
        "entry_id",
        "symbol",
        "entry_tf",
        "entry_strategy",
        "entry_price",
    }

    missing = (
        required
        - cols
    )

    if missing:
        raise RuntimeError(
            "EXIT_HISTORY_TARGET_FIELDS_MISSING="
            + repr(
                sorted(missing)
            )
        )

    wanted = [
        c
        for c in (
            "entry_id",
            "symbol",
            "side",
            "position_side",
            "entry_tf",
            "entry_strategy",
            "entry_strategy_version",
            "entry_price",
            "qty",
            "entry_leverage",
            "signal_id",
            "signal_candle_time",
            "entry_ts",
            "entry_time",
            "entry_utc",
            "opened_at",
            "open_ts",
            "planned_sl_price",
            "sl_distance_pct",
        )
        if c in cols
    ]

    rows = table_rows(
        "exit_history",
        wanted,
    )

    entry_times = (
        entry_time_map()
    )

    direct_time_fields = [
        name
        for name in (
            "entry_ts",
            "entry_time",
            "entry_utc",
            "opened_at",
            "open_ts",
            "signal_candle_time",
        )
        if name in cols
    ]

    targets = []

    seen = set()

    for row in rows:
        entry_id = str(
            row.get("entry_id")
            or ""
        ).strip()

        if (
            not entry_id
            or entry_id in seen
        ):
            continue

        seen.add(
            entry_id
        )

        target_epoch = None

        for field in direct_time_fields:
            x = epoch(
                row.get(field)
            )

            if x is not None:
                target_epoch = x
                break

        if target_epoch is None:
            target_epoch = (
                entry_times.get(
                    entry_id
                )
            )

        if target_epoch is None:
            continue

        row[
            "_target_epoch"
        ] = target_epoch

        targets.append(
            row
        )

    return targets


def load_prior_source():
    prior = json.loads(
        PRIOR.read_text(
            encoding="utf-8-sig"
        )
    )

    if (
        prior.get("status")
        !=
        "HISTORICAL_PRIOR_RESEARCH_ONLY"
    ):
        raise RuntimeError(
            "PRIOR_STATUS_INVALID"
        )

    if (
        prior.get("policy_effect")
        !=
        "NONE_UNTIL_SEPARATE_VALIDATION_AND_POLICY_VERSION"
    ):
        raise RuntimeError(
            "PRIOR_POLICY_EFFECT_INVALID"
        )

    sources = (
        prior.get("sources")
        or {}
    )

    spec = sources.get(
        "trade_mae"
    )

    if not isinstance(
        spec,
        dict,
    ):
        raise RuntimeError(
            "TRADE_MAE_SOURCE_MISSING"
        )

    source = (
        ROOT
        / str(
            spec.get("path")
            or ""
        )
    )

    expected_sha = str(
        spec.get("sha256")
        or ""
    )

    if not source.is_file():
        raise RuntimeError(
            "TRADE_MAE_FILE_MISSING"
        )

    actual_sha = sha256(
        source
    )

    if (
        not expected_sha
        or expected_sha
        != actual_sha
    ):
        raise RuntimeError(
            "TRADE_MAE_SHA_MISMATCH"
        )

    return (
        source,
        actual_sha,
    )


def training_rows(
    source: Path,
):
    result = []

    with source.open(
        "r",
        encoding="utf-8-sig",
        errors="replace",
        newline="",
    ) as f:
        reader = csv.DictReader(f)

        required = {
            "symbol",
            "side",
            "owner_tf",
            "entry_ts",
            "exit_ts",
            "exit_reason",
            "mae_abs_pct",
        }

        missing = (
            required
            - set(
                reader.fieldnames
                or []
            )
        )

        if missing:
            raise RuntimeError(
                "TRAINING_COLUMNS_MISSING="
                + repr(
                    sorted(missing)
                )
            )

        for row in reader:
            exit_epoch = epoch(
                row.get(
                    "exit_ts"
                )
            )

            mae = finite(
                row.get(
                    "mae_abs_pct"
                )
            )

            if (
                exit_epoch is None
                or mae is None
            ):
                continue

            reason = str(
                row.get(
                    "exit_reason"
                )
                or ""
            ).upper()

            result.append(
                {
                    "symbol":
                        str(
                            row.get(
                                "symbol"
                            )
                            or ""
                        ).upper(),

                    "side":
                        side_name(
                            row.get(
                                "side"
                            )
                        ),

                    "timeframe":
                        str(
                            row.get(
                                "owner_tf"
                            )
                            or ""
                        ).upper(),

                    "exit_epoch":
                        exit_epoch,

                    "mae_abs_pct":
                        abs(mae),

                    "stop_label":
                        (
                            1.0
                            if (
                                "STOP"
                                in reason
                                or
                                "CONTROL_SL"
                                in reason
                            )
                            else 0.0
                        ),
                }
            )

    return result


def mean(values):
    if not values:
        return None

    return (
        sum(
            float(v)
            for v in values
        )
        / len(values)
    )


def predict(
    target,
    training,
):
    """Point-in-time empirical shadow baseline.

    Scope ladder is deliberately broadening-only when a more specific
    group has insufficient completed history.

    Every scope uses ONLY trades whose exit timestamp is strictly
    earlier than the target entry timestamp.

    Normal scopes require >=20 samples.
    GLOBAL requires >=50 samples.
    """

    target_epoch = target[
        "_target_epoch"
    ]

    symbol = str(
        target.get(
            "symbol"
        )
        or ""
    ).upper()

    side = side_name(
        target.get("side")
        or target.get(
            "position_side"
        )
    )

    timeframe = str(
        target.get(
            "entry_tf"
        )
        or ""
    ).upper()

    # Absolutely no future leakage.
    pit_rows = [
        row
        for row in training
        if (
            row[
                "exit_epoch"
            ]
            < target_epoch
        )
    ]

    symbol_tf_side = [
        row
        for row in pit_rows
        if (
            row[
                "symbol"
            ]
            == symbol
            and
            row[
                "timeframe"
            ]
            == timeframe
            and
            row[
                "side"
            ]
            == side
        )
    ]

    tf_side = [
        row
        for row in pit_rows
        if (
            row[
                "timeframe"
            ]
            == timeframe
            and
            row[
                "side"
            ]
            == side
        )
    ]

    tf_rows = [
        row
        for row in pit_rows
        if (
            row[
                "timeframe"
            ]
            == timeframe
        )
    ]

    side_rows = [
        row
        for row in pit_rows
        if (
            row[
                "side"
            ]
            == side
        )
    ]

    if (
        len(symbol_tf_side)
        >= MIN_TRAIN_SAMPLES
    ):
        model_rows = (
            symbol_tf_side
        )

        scope = (
            "SYMBOL_TIMEFRAME_SIDE"
        )

    elif (
        len(tf_side)
        >= MIN_TRAIN_SAMPLES
    ):
        model_rows = tf_side

        scope = (
            "TIMEFRAME_SIDE"
        )

    elif (
        len(tf_rows)
        >= MIN_TRAIN_SAMPLES
    ):
        model_rows = tf_rows

        scope = "TIMEFRAME"

    elif (
        len(side_rows)
        >= MIN_TRAIN_SAMPLES
    ):
        model_rows = side_rows

        scope = "SIDE"

    elif (
        len(pit_rows)
        >= MIN_GLOBAL_TRAIN_SAMPLES
    ):
        model_rows = pit_rows

        scope = "GLOBAL"

    else:
        return None

    stop = mean(
        [
            row[
                "stop_label"
            ]
            for row in model_rows
        ]
    )

    adverse = mean(
        [
            row[
                "mae_abs_pct"
            ]
            for row in model_rows
        ]
    )

    if (
        stop is None
        or adverse is None
    ):
        return None

    return {
        "training_scope":
            scope,

        "training_samples":
            len(model_rows),

        "pit_available_samples":
            len(pit_rows),

        "symbol_timeframe_side_samples":
            len(
                symbol_tf_side
            ),

        "timeframe_side_samples":
            len(tf_side),

        "timeframe_samples":
            len(tf_rows),

        "side_samples":
            len(side_rows),

        "predicted_stop_hit_probability":
            None,

        "predicted_expected_adverse_move":
            adverse,
    }



def existing_keys():
    result = set()

    if not OUT.is_file():
        return result

    for raw in OUT.read_text(
        encoding="utf-8-sig",
        errors="replace",
    ).splitlines():

        try:
            row = json.loads(
                raw
            )
        except Exception:
            continue

        key = (
            str(
                row.get(
                    "entry_id"
                )
                or ""
            ),
            str(
                row.get(
                    "model_version"
                )
                or ""
            ),
        )

        if all(key):
            result.add(
                key
            )

    return result


def build():
    source, source_sha = (
        load_prior_source()
    )

    training = training_rows(
        source
    )

    targets = target_exits()

    existing = existing_keys()

    rows = []

    skipped_training = 0
    skipped_duplicate = 0

    generated_at = (
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    for target in targets:
        entry_id = str(
            target.get(
                "entry_id"
            )
            or ""
        ).strip()

        key = (
            entry_id,
            MODEL_VERSION,
        )

        if key in existing:
            skipped_duplicate += 1
            continue

        model = predict(
            target,
            training,
        )

        if model is None:
            skipped_training += 1
            continue

        target_epoch = float(
            target[
                "_target_epoch"
            ]
        )

        rows.append(
            {
                "schema":
                    SCHEMA,

                "evidence_class":
                    EVIDENCE_CLASS,

                "model_version":
                    MODEL_VERSION,

                "effect_mode":
                    "SHADOW",

                "live_effect":
                    False,

                "policy_effect":
                    "NONE",

                "policy_version":
                    "PV_ENTRY_RISK_BASELINE_V1",

                "calibration_status":
                    "UNVALIDATED_RETROSPECTIVE_POINT_IN_TIME_REPLAY",

                "entry_id":
                    entry_id,

                "candidate_id":
                    entry_id,

                # Point-in-time observation instant.
                "observed_at":
                    iso_utc(
                        target_epoch
                    ),

                # Actual generation instant is kept separate.
                "generated_at":
                    generated_at,

                "prediction_asof":
                    iso_utc(
                        target_epoch
                    ),

                "prediction_asof_epoch":
                    target_epoch,

                "symbol":
                    str(
                        target.get(
                            "symbol"
                        )
                        or ""
                    ).upper(),

                "side":
                    side_name(
                        target.get(
                            "side"
                        )
                        or target.get(
                            "position_side"
                        )
                    ),

                "timeframe":
                    str(
                        target.get(
                            "entry_tf"
                        )
                        or ""
                    ).upper(),

                "strategy":
                    str(
                        target.get(
                            "entry_strategy"
                        )
                        or ""
                    ),

                "strategy_version":
                    target.get(
                        "entry_strategy_version"
                    ),

                "signal_id":
                    str(
                        target.get(
                            "signal_id"
                        )
                        or ""
                    ),

                "signal_candle_time":
                    target.get(
                        "signal_candle_time"
                    ),

                "current_price":
                    finite(
                        target.get(
                            "entry_price"
                        )
                    ),

                "signal_price":
                    None,

                "signal_distance_pct":
                    None,

                "remaining_expected_edge":
                    None,

                "planned_sl_price":
                    finite(
                        target.get(
                            "planned_sl_price"
                        )
                    ),

                "sl_distance_pct":
                    finite(
                        target.get(
                            "sl_distance_pct"
                        )
                    ),

                "predicted_entry_risk_score":
                    None,

                "predicted_stop_hit_probability":
                    None,

                "predicted_expected_adverse_move":
                    model[
                        "predicted_expected_adverse_move"
                    ],

                # Not estimated by this baseline.
                "predicted_safe_size_qty":
                    None,

                "predicted_safe_leverage":
                    None,

                "training_scope":
                    model[
                        "training_scope"
                    ],

                "training_samples":
                    model[
                        "training_samples"
                    ],

                "training_rule":
                    "TRAINING_EXIT_TS_STRICTLY_BEFORE_TARGET_ENTRY_TS",

                "training_source":
                    str(
                        source.relative_to(
                            ROOT
                        )
                    ),

                "training_source_sha256":
                    source_sha,

                "historical_prior_artifact_used_as_prediction":
                    False,

                "retrospective_only":
                    True,
            }
        )

    return {
        "rows":
            rows,

        "targets":
            len(targets),

        "training_rows":
            len(training),

        "existing_rows":
            len(existing),

        "skipped_duplicate":
            skipped_duplicate,

        "skipped_insufficient_training":
            skipped_training,
    }


def write_rows(rows):
    if not rows:
        return

    OUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    old = ""

    if OUT.is_file():
        old = OUT.read_text(
            encoding="utf-8-sig",
            errors="replace",
        )

        if (
            old
            and
            not old.endswith("\n")
        ):
            old += "\n"

    additions = "".join(
        json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n"
        for row in rows
    )

    tmp = OUT.with_suffix(
        ".jsonl.tmp"
    )

    tmp.write_text(
        old
        + additions,
        encoding="utf-8",
        newline="\n",
    )

    os.replace(
        tmp,
        OUT,
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--write",
        action="store_true",
    )

    args = parser.parse_args()

    result = build()

    if args.write:
        write_rows(
            result["rows"]
        )

    print(
        json.dumps(
            {
                "schema":
                    "B_BOT_V3_ENTRY_RISK_SHADOW_PRODUCER_RESULT_V1",

                "model_version":
                    MODEL_VERSION,

                "evidence_class":
                    EVIDENCE_CLASS,

                "write":
                    bool(
                        args.write
                    ),

                "produced_rows":
                    len(
                        result["rows"]
                    ),

                "target_entries":
                    result["targets"],

                "training_rows":
                    result[
                        "training_rows"
                    ],

                "existing_rows":
                    result[
                        "existing_rows"
                    ],

                "skipped_duplicate":
                    result[
                        "skipped_duplicate"
                    ],

                "skipped_insufficient_training":
                    result[
                        "skipped_insufficient_training"
                    ],

                "live_effect":
                    False,

                "runner_binding":
                    False,

                "policy_version_promotion":
                    False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
