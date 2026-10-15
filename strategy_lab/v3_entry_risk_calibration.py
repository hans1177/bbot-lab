# -*- coding: utf-8 -*-
# 파일명: strategy_lab/v3_entry_risk_calibration.py
"""ENTRY_RISK predicted-vs-realized calibration 연구.

새 DB 테이블을 만들지 않고 기존 exit_history를 읽기만 한다.
충분한 paired evidence가 없으면 추정치를 억지로 생성하지 않는다.
"""
from __future__ import annotations

# ============================================================
# 임포트
# ============================================================

import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ============================================================
# 경로 / 상수
# ============================================================

ROOT = Path(os.getenv("BBOT_LOCAL_ROOT", r"C:\my\bottest"))
DB = ROOT / "data" / "server_runtime" / "history.db"
OUT = ROOT / "strategy_lab" / "results" / "v3" / "entry_risk_calibration.json"
MIN_PAIRED_SAMPLES = 20
MAX_HISTORY = 200

# ============================================================
# 유틸
# ============================================================


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load(path: Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def atomic(path: Path, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def columns() -> set[str]:
    if not DB.is_file():
        return set()
    con = sqlite3.connect(str(DB), timeout=10)
    try:
        return {str(r[1]) for r in con.execute("PRAGMA table_info(exit_history)").fetchall()}
    except Exception:
        return set()
    finally:
        con.close()


def _corr(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(vx * vy)


# ============================================================
# Calibration
# ============================================================




# CANDIDATE_ENTRY_RISK_SHADOW_EVIDENCE_V1
SHADOW_EVIDENCE = (
    ROOT
    / "strategy_lab"
    / "results"
    / "v3"
    / "candidate_entry_risk_shadow_evidence.jsonl"
)

SHADOW_SCHEMA = (
    "B_BOT_V3_CANDIDATE_ENTRY_RISK_SHADOW_EVIDENCE_V1"
)

MIN_PAIRED_SAMPLES = 20


def _finite_or_none(value):
    import math

    try:
        value = float(value)
    except Exception:
        return None

    if not math.isfinite(value):
        return None

    return value


def _load_shadow_evidence():
    import json

    rows = []

    if not SHADOW_EVIDENCE.is_file():
        return rows

    for raw in SHADOW_EVIDENCE.read_text(
        encoding="utf-8-sig",
        errors="replace",
    ).splitlines():

        raw = raw.strip()

        if not raw:
            continue

        try:
            row = json.loads(raw)
        except Exception:
            continue

        if not isinstance(row, dict):
            continue

        if (
            row.get("schema")
            != SHADOW_SCHEMA
        ):
            continue

        if (
            row.get("effect_mode")
            != "SHADOW"
        ):
            continue

        if (
            row.get("live_effect")
            is not False
        ):
            continue

        rows.append(row)

    return rows


def _shadow_match_key(row):
    entry_id = str(
        row.get("entry_id")
        or ""
    ).strip()

    if entry_id:
        return (
            "ENTRY_ID",
            entry_id,
        )

    return (
        "COMPOSITE",
        str(
            row.get("symbol")
            or ""
        ).upper(),
        str(
            row.get("side")
            or ""
        ).upper(),
        str(
            row.get("timeframe")
            or row.get("entry_tf")
            or ""
        ).upper(),
        str(
            row.get("strategy")
            or row.get("entry_strategy")
            or ""
        ),
        str(
            row.get("signal_id")
            or ""
        ),
    )


def _exit_match_key(row):
    entry_id = str(
        row.get("entry_id")
        or ""
    ).strip()

    if entry_id:
        return (
            "ENTRY_ID",
            entry_id,
        )

    return (
        "COMPOSITE",
        str(
            row.get("symbol")
            or ""
        ).upper(),
        str(
            row.get("side")
            or row.get("position_side")
            or ""
        ).upper(),
        str(
            row.get("entry_tf")
            or ""
        ).upper(),
        str(
            row.get("entry_strategy")
            or ""
        ),
        str(
            row.get("signal_id")
            or ""
        ),
    )


def _mean(values):
    clean = [
        number
        for number in (
            _finite_or_none(v)
            for v in values
        )
        if number is not None
    ]

    if not clean:
        return None

    return (
        sum(clean)
        / len(clean)
    )


def _pearson(xs, ys):
    pairs = []

    for x, y in zip(
        xs,
        ys,
    ):
        xf = _finite_or_none(x)
        yf = _finite_or_none(y)

        if (
            xf is None
            or yf is None
        ):
            continue

        pairs.append(
            (
                xf,
                yf,
            )
        )

    if len(pairs) < 2:
        return None

    xvals = [
        x
        for x, _
        in pairs
    ]

    yvals = [
        y
        for _, y
        in pairs
    ]

    xm = (
        sum(xvals)
        / len(xvals)
    )

    ym = (
        sum(yvals)
        / len(yvals)
    )

    num = sum(
        (x - xm)
        * (y - ym)
        for x, y
        in pairs
    )

    dx = sum(
        (x - xm) ** 2
        for x
        in xvals
    )

    dy = sum(
        (y - ym) ** 2
        for y
        in yvals
    )

    den = (
        dx * dy
    ) ** 0.5

    if den <= 0:
        return None

    return (
        num
        / den
    )


# CANDIDATE_ENTRY_RISK_CALIBRATION_DB_HELPERS_V1
def _calibration_history_db():
    import sqlite3

    path = (
        ROOT
        / "data"
        / "server_runtime"
        / "history.db"
    )

    if not path.is_file():
        raise RuntimeError(
            "ENTRY_RISK_HISTORY_DB_MISSING="
            + str(path)
        )

    uri = (
        path.resolve().as_uri()
        + "?mode=ro"
    )

    con = sqlite3.connect(
        uri,
        uri=True,
    )

    try:
        row = con.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type='table'
              AND name='exit_history'
            LIMIT 1
            """
        ).fetchone()

        if row is None:
            raise RuntimeError(
                "EXIT_HISTORY_TABLE_MISSING"
            )

    finally:
        con.close()

    return path


def table_columns():
    import sqlite3

    path = (
        _calibration_history_db()
    )

    uri = (
        path.resolve().as_uri()
        + "?mode=ro"
    )

    con = sqlite3.connect(
        uri,
        uri=True,
    )

    try:
        rows = con.execute(
            "PRAGMA table_info(exit_history)"
        ).fetchall()

        return {
            str(row[1])
            for row in rows
        }

    finally:
        con.close()


def query(sql, params=()):
    import sqlite3

    path = (
        _calibration_history_db()
    )

    uri = (
        path.resolve().as_uri()
        + "?mode=ro"
    )

    con = sqlite3.connect(
        uri,
        uri=True,
    )

    con.row_factory = sqlite3.Row

    try:
        rows = con.execute(
            sql,
            tuple(params or ()),
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]

    finally:
        con.close()
def save(path, obj):
    import json
    import os

    path = Path(path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp = path.with_suffix(
        path.suffix
        + ".tmp"
    )

    tmp.write_text(
        json.dumps(
            obj,
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
        path,
    )

def measure() -> dict[str, Any]:
    """Measure genuine forward predicted-vs-realized Entry Risk evidence.

    Legacy retrospective shadow evidence remains preserved for audit but
    is not counted toward forward calibration.

    Adverse calibration:
      forward prediction record
      + exact entry_id forward outcome
      + outcome quality_status == USABLE

    STOP calibration requires both:
      numeric prediction made before outcome
      + observed exit label

    Until STOP prediction exists, STOP pairs remain zero and policy
    promotion remains fail-closed.
    """

    forward_prediction_path = (
        ROOT
        / "strategy_lab"
        / "results"
        / "v3"
        / "candidate_entry_risk_forward_shadow_evidence.jsonl"
    )

    forward_outcome_path = (
        ROOT
        / "strategy_lab"
        / "results"
        / "v3"
        / "candidate_entry_risk_forward_outcomes.jsonl"
    )

    legacy_path = (
        ROOT
        / "strategy_lab"
        / "results"
        / "v3"
        / "candidate_entry_risk_shadow_evidence.jsonl"
    )

    def load_jsonl(path):
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

    predictions = load_jsonl(
        forward_prediction_path
    )

    outcomes = load_jsonl(
        forward_outcome_path
    )

    legacy_rows = load_jsonl(
        legacy_path
    )

    pred_map = {}

    duplicate_predictions = set()

    for row in predictions:
        entry_id = str(
            row.get("entry_id")
            or ""
        ).strip()

        if not entry_id:
            continue

        if entry_id in pred_map:
            duplicate_predictions.add(
                entry_id
            )

        pred_map[
            entry_id
        ] = row

    outcome_map = {}

    duplicate_outcomes = set()

    for row in outcomes:
        entry_id = str(
            row.get("entry_id")
            or ""
        ).strip()

        if not entry_id:
            continue

        if entry_id in outcome_map:
            duplicate_outcomes.add(
                entry_id
            )

        outcome_map[
            entry_id
        ] = row

    adverse_pairs = []
    stop_pairs = []

    rejected = {
        "duplicate_prediction":
            0,

        "duplicate_outcome":
            0,

        "outcome_not_usable":
            0,

        "prediction_not_forward":
            0,

        "prediction_live_effect":
            0,

        "missing_predicted_adverse":
            0,

        "missing_realized_mae":
            0,
    }

    for entry_id, pred in pred_map.items():

        outcome = outcome_map.get(
            entry_id
        )

        if outcome is None:
            continue

        if (
            entry_id
            in duplicate_predictions
        ):
            rejected[
                "duplicate_prediction"
            ] += 1
            continue

        if (
            entry_id
            in duplicate_outcomes
        ):
            rejected[
                "duplicate_outcome"
            ] += 1
            continue

        if (
            outcome.get(
                "quality_status"
            )
            != "USABLE"
        ):
            rejected[
                "outcome_not_usable"
            ] += 1
            continue

        if (
            pred.get(
                "forward_only"
            )
            is not True
            or
            pred.get(
                "outcome_known_at_prediction"
            )
            is not False
        ):
            rejected[
                "prediction_not_forward"
            ] += 1
            continue

        if (
            pred.get(
                "live_effect"
            )
            is not False
        ):
            rejected[
                "prediction_live_effect"
            ] += 1
            continue

        pred_mae = _finite_or_none(
            pred.get(
                "predicted_expected_adverse_move"
            )
        )

        realized_mae = _finite_or_none(
            outcome.get(
                "realized_mae"
            )
        )

        if pred_mae is None:
            rejected[
                "missing_predicted_adverse"
            ] += 1

        elif realized_mae is None:
            rejected[
                "missing_realized_mae"
            ] += 1

        else:
            adverse_pairs.append(
                {
                    "entry_id":
                        entry_id,

                    "predicted":
                        pred_mae,

                    "realized":
                        abs(
                            realized_mae
                        ),
                }
            )

        pred_stop = _finite_or_none(
            pred.get(
                "predicted_stop_hit_probability"
            )
        )

        realized_stop = _finite_or_none(
            outcome.get(
                "stop_label"
            )
        )

        if (
            pred_stop is not None
            and
            realized_stop is not None
        ):
            stop_pairs.append(
                {
                    "entry_id":
                        entry_id,

                    "predicted":
                        pred_stop,

                    "realized":
                        realized_stop,
                }
            )

    result = {
        "status":
            "ACCUMULATE_FIRST",

        "effect_mode":
            "SHADOW",

        "live_effect":
            False,

        "policy_effect":
            "NONE_UNTIL_VALIDATED_POLICY_VERSION",

        "evidence_mode":
            "FORWARD_ONLY",

        "forward_prediction_path":
            str(
                forward_prediction_path.relative_to(
                    ROOT
                )
            ),

        "forward_outcome_path":
            str(
                forward_outcome_path.relative_to(
                    ROOT
                )
            ),

        "forward_prediction_rows":
            len(predictions),

        "forward_outcome_rows":
            len(outcomes),

        "legacy_shadow_evidence_path":
            "strategy_lab\\results\\v3\\candidate_entry_risk_shadow_evidence.jsonl",

        "legacy_shadow_rows":
            len(legacy_rows),

        "legacy_shadow_evidence_used":
            False,

        "paired_samples":
            len(adverse_pairs),

        "stop_paired_samples":
            len(stop_pairs),

        "minimum_paired_samples":
            MIN_PAIRED_SAMPLES,

        "rejected":
            rejected,

        "predicted_adverse_avg":
            None,

        "realized_mae_abs_avg":
            None,

        "adverse_mae_error":
            None,

        "adverse_corr":
            None,

        "predicted_stop_avg":
            None,

        "realized_stop_rate":
            None,

        "stop_brier_score":
            None,

        "safe_size_error_avg":
            None,

        "safe_leverage_error_avg":
            None,
    }

    if adverse_pairs:

        result[
            "predicted_adverse_avg"
        ] = _mean(
            [
                row["predicted"]
                for row in adverse_pairs
            ]
        )

        result[
            "realized_mae_abs_avg"
        ] = _mean(
            [
                row["realized"]
                for row in adverse_pairs
            ]
        )

        result[
            "adverse_mae_error"
        ] = _mean(
            [
                abs(
                    row["predicted"]
                    - row["realized"]
                )
                for row in adverse_pairs
            ]
        )

        result[
            "adverse_corr"
        ] = _pearson(
            [
                row["predicted"]
                for row in adverse_pairs
            ],
            [
                row["realized"]
                for row in adverse_pairs
            ],
        )

    if stop_pairs:

        result[
            "predicted_stop_avg"
        ] = _mean(
            [
                row["predicted"]
                for row in stop_pairs
            ]
        )

        result[
            "realized_stop_rate"
        ] = _mean(
            [
                row["realized"]
                for row in stop_pairs
            ]
        )

        result[
            "stop_brier_score"
        ] = _mean(
            [
                (
                    row["predicted"]
                    - row["realized"]
                ) ** 2
                for row in stop_pairs
            ]
        )

    if (
        len(adverse_pairs)
        < MIN_PAIRED_SAMPLES
    ):
        result[
            "status"
        ] = "ACCUMULATE_FIRST"

        result[
            "missing"
        ] = [
            "minimum usable forward adverse pairs"
        ]

        return result

    if (
        len(stop_pairs)
        < MIN_PAIRED_SAMPLES
    ):
        result[
            "status"
        ] = (
            "ADVERSE_MEASURED_STOP_UNAVAILABLE"
        )

        result[
            "missing"
        ] = [
            "minimum usable forward stop prediction pairs"
        ]

        return result

    result[
        "status"
    ] = "MEASURED"

    result[
        "missing"
    ] = []

    return result





def run_once() -> dict[str, Any]:
    result = measure()

    state = load(
        OUT,
        {
            "schema":
                "B_BOT_V3_ENTRY_RISK_CALIBRATION_V1",

            "history":
                [],
        },
    )

    history = (
        state.get("history")
        if isinstance(
            state.get("history"),
            list,
        )
        else []
    )

    now = (
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    history.append(
        {
            "at":
                now,

            "result":
                result,
        }
    )

    history = history[-200:]

    state[
        "schema"
    ] = (
        "B_BOT_V3_ENTRY_RISK_CALIBRATION_V1"
    )

    state[
        "history"
    ] = history

    state[
        "latest"
    ] = result

    state[
        "updated_at"
    ] = now

    save(
        OUT,
        state,
    )

    return result



if __name__ == "__main__":
    print(json.dumps(run_once(), ensure_ascii=False, indent=2, default=str))
