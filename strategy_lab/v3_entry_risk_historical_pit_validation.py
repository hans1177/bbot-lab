from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import csv
import hashlib
import json
import math
import os
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
V3 = ROOT / "strategy_lab" / "results" / "v3"

PRIOR = (
    V3
    / "entry_risk_historical_prior.json"
)

OUT = (
    V3
    / "entry_risk_historical_pit_validation.json"
)

SCHEMA = (
    "B_BOT_V3_ENTRY_RISK_HISTORICAL_PIT_VALIDATION_V1"
)

EVIDENCE_CLASS = (
    "RETROSPECTIVE_POINT_IN_TIME_WALK_FORWARD"
)

MODEL_VERSION = (
    "ER_EMPIRICAL_PIT_V1"
)

MIN_TRAIN = 20
MIN_GLOBAL = 50


def sha256(path: Path) -> str:
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def finite(value: Any):
    try:
        x = float(value)
    except Exception:
        return None

    if not math.isfinite(x):
        return None

    return x


def epoch(value: Any):
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


def side_name(value: Any):
    s = str(
        value
        or ""
    ).strip().upper()

    if s in {
        "BUY",
        "LONG",
        "1",
        "1.0",
    }:
        return "LONG"

    if s in {
        "SELL",
        "SHORT",
        "-1",
        "-1.0",
    }:
        return "SHORT"

    return s


def mean(values):
    values = list(values)

    if not values:
        return None

    return (
        sum(
            float(v)
            for v in values
        )
        / len(values)
    )


def corr(xs, ys):
    pairs = [
        (float(x), float(y))
        for x, y in zip(xs, ys)
        if (
            x is not None
            and y is not None
        )
    ]

    if len(pairs) < 2:
        return None

    ax = mean(
        x for x, _ in pairs
    )

    ay = mean(
        y for _, y in pairs
    )

    num = sum(
        (x - ax)
        * (y - ay)
        for x, y in pairs
    )

    dx = sum(
        (x - ax) ** 2
        for x, _ in pairs
    )

    dy = sum(
        (y - ay) ** 2
        for _, y in pairs
    )

    den = (
        dx * dy
    ) ** 0.5

    if den <= 0:
        return None

    return num / den


def load_source():
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

    spec = (
        (
            prior.get("sources")
            or {}
        ).get("trade_mae")
        or {}
    )

    path = (
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

    if not path.is_file():
        raise RuntimeError(
            "SOURCE_MISSING"
        )

    actual_sha = sha256(
        path
    )

    if (
        not expected_sha
        or actual_sha
        != expected_sha
    ):
        raise RuntimeError(
            "SOURCE_SHA_MISMATCH"
        )

    return path, actual_sha


def load_rows(path):
    result = []

    with path.open(
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
                "SOURCE_FIELDS_MISSING="
                + repr(
                    sorted(missing)
                )
            )

        for row in reader:

            entry_epoch = epoch(
                row.get("entry_ts")
            )

            exit_epoch = epoch(
                row.get("exit_ts")
            )

            mae = finite(
                row.get(
                    "mae_abs_pct"
                )
            )

            if (
                entry_epoch is None
                or
                exit_epoch is None
                or
                mae is None
            ):
                continue

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

                    "entry_epoch":
                        entry_epoch,

                    "exit_epoch":
                        exit_epoch,

                    "mae":
                        abs(mae),

                    "exit_reason":
                        str(
                            row.get(
                                "exit_reason"
                            )
                            or ""
                        ).upper(),
                }
            )

    result.sort(
        key=lambda x: (
            x["entry_epoch"],
            x["exit_epoch"],
        )
    )

    return result



def prediction(
    target,
    history,
):
    # Strict no-leak point-in-time rule.
    pit = [
        row
        for row in history
        if (
            row["exit_epoch"]
            < target["entry_epoch"]
        )
    ]

    symbol_tf_side = [
        row
        for row in pit
        if (
            row["symbol"]
            == target["symbol"]
            and
            row["timeframe"]
            == target["timeframe"]
            and
            row["side"]
            == target["side"]
        )
    ]

    tf_side = [
        row
        for row in pit
        if (
            row["timeframe"]
            == target["timeframe"]
            and
            row["side"]
            == target["side"]
        )
    ]

    tf_rows = [
        row
        for row in pit
        if (
            row["timeframe"]
            == target["timeframe"]
        )
    ]

    side_rows = [
        row
        for row in pit
        if (
            row["side"]
            == target["side"]
        )
    ]

    if (
        len(symbol_tf_side)
        >= MIN_TRAIN
    ):
        rows = symbol_tf_side
        scope = "SYMBOL_TIMEFRAME_SIDE"

    elif (
        len(tf_side)
        >= MIN_TRAIN
    ):
        rows = tf_side
        scope = "TIMEFRAME_SIDE"

    elif (
        len(tf_rows)
        >= MIN_TRAIN
    ):
        rows = tf_rows
        scope = "TIMEFRAME"

    elif (
        len(side_rows)
        >= MIN_TRAIN
    ):
        rows = side_rows
        scope = "SIDE"

    elif (
        len(pit)
        >= MIN_GLOBAL
    ):
        rows = pit
        scope = "GLOBAL"

    else:
        return None

    return {
        "scope":
            scope,

        "training_samples":
            len(rows),

        "pred_mae":
            mean(
                row["mae"]
                for row in rows
            ),
    }



def run():
    source, source_sha = (
        load_source()
    )

    rows = load_rows(
        source
    )

    pairs = []
    scope_counts = {}

    for target in rows:

        pred = prediction(
            target,
            rows,
        )

        if pred is None:
            continue

        scope_counts[
            pred["scope"]
        ] = (
            scope_counts.get(
                pred["scope"],
                0,
            )
            + 1
        )

        pairs.append(
            {
                "pred_mae":
                    pred["pred_mae"],

                "real_mae":
                    target["mae"],
            }
        )

    adverse_error = mean(
        abs(
            p["pred_mae"]
            - p["real_mae"]
        )
        for p in pairs
    )

    adverse_bias = mean(
        (
            p["pred_mae"]
            - p["real_mae"]
        )
        for p in pairs
    )

    adverse_corr = corr(
        [
            p["pred_mae"]
            for p in pairs
        ],
        [
            p["real_mae"]
            for p in pairs
        ],
    )

    result = {
        "schema":
            SCHEMA,

        "status":
            (
                "RETROSPECTIVE_ADVERSE_MEASURED_STOP_UNAVAILABLE"
                if len(pairs) >= 20
                else "ACCUMULATE_FIRST"
            ),

        "evidence_class":
            EVIDENCE_CLASS,

        "model_version":
            MODEL_VERSION,

        "source":
            str(
                source.relative_to(
                    ROOT
                )
            ),

        "source_sha256":
            source_sha,

        "source_rows":
            len(rows),

        "paired_samples":
            len(pairs),

        "adverse_paired_samples":
            len(pairs),

        "stop_paired_samples":
            0,

        "scope_counts":
            scope_counts,

        "strict_point_in_time":
            True,

        "training_rule":
            "TRAINING_EXIT_TS_STRICTLY_BEFORE_TARGET_ENTRY_TS",

        "retrospective_only":
            True,

        "independent_validation":
            False,

        "policy_candidate_allowed":
            False,

        "live_effect":
            False,

        "policy_effect":
            "NONE",

        "historical_exit_label_set":
            [
                "OPPOSITE_SIGNAL",
                "OOS_END",
            ],

        "stop_evidence_status":
            "UNAVAILABLE_SOURCE_HAS_NO_STOP_LABELS",

        "predicted_stop_avg":
            None,

        "realized_stop_rate":
            None,

        "stop_brier_score":
            None,

        "predicted_adverse_avg":
            mean(
                p["pred_mae"]
                for p in pairs
            ),

        "realized_mae_avg":
            mean(
                p["real_mae"]
                for p in pairs
            ),

        "adverse_mae_error":
            adverse_error,

        "adverse_bias":
            adverse_bias,

        "adverse_corr":
            adverse_corr,

        "generated_at":
            datetime.now(
                timezone.utc
            ).isoformat(),
    }

    OUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp = OUT.with_suffix(
        ".json.tmp"
    )

    tmp.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    os.replace(
        tmp,
        OUT,
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )



if __name__ == "__main__":
    run()
