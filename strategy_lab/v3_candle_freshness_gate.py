from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

import pandas as pd


ROOT = Path(
    os.environ.get(
        "BBOT_LOCAL_ROOT"
    )
    or r"C:\my\bottest"
)

RESULTS = (
    ROOT
    / "strategy_lab"
    / "results"
    / "v3"
)

REQUIRED = (
    RESULTS
    / "collector_required_universe.json"
)

HEARTBEAT = (
    RESULTS
    / "collector_heartbeat.json"
)

CANDLES = (
    ROOT
    / "data"
    / "candles"
)

TF_SECONDS = {
    "30M": 1800,
    "1H": 3600,
    "6H": 21600,
    "12H": 43200,
    "1D": 86400,
    "1W": 604800,
}


def load(path, default):

    try:

        return json.loads(
            Path(path).read_text(
                encoding="utf-8-sig"
            )
        )

    except Exception:

        return default


def epoch(value):

    if isinstance(
        value,
        pd.Timestamp,
    ):
        return float(
            value.timestamp()
        )

    try:

        value = float(value)

        if abs(value) > 10000000000:
            value /= 1000.0

        return value

    except Exception:

        return float(
            pd.Timestamp(
                value
            ).timestamp()
        )


def paths(symbol, timeframe):

    tf = str(
        timeframe
    ).upper()

    names = {
        "1H": ["1h.parquet", "1H.parquet"],
        "6H": ["6h.parquet", "6H.parquet"],
        "12H": ["12h.parquet", "12H.parquet"],
        "1D": ["1d.parquet", "1D.parquet"],
        "1W": ["1w.parquet", "1W.parquet"],
    }.get(
        tf,
        [
            tf.lower() + ".parquet",
            tf + ".parquet",
        ],
    )

    return [
        CANDLES
        / symbol
        / name
        for name in names
    ]


def check_candle_freshness(
    timeframe="1H",
):

    # stale fail closed
    # missing symbol fail closed
    # collector down fail closed
    # closed_bar / is_closed required
    # OHLCV = open high low close volume

    tf = str(
        timeframe or "1H"
    ).upper()

    seconds = int(
        TF_SECONDS.get(tf)
        or 3600
    )

    now = time.time()

    max_age = max(
        21600,
        float(seconds) * 2.2,
    )

    required = load(
        REQUIRED,
        {},
    )

    symbols = (
        required.get(
            "required_universe"
        )
        or required.get(
            "required_symbols"
        )
        or []
    )

    if not symbols:

        return {
            "pass": False,
            "reason":
                "REQUIRED_UNIVERSE_MISSING",
            "details": [],
        }

    heartbeat = load(
        HEARTBEAT,
        {},
    )

    try:

        heartbeat_ts = float(
            heartbeat.get(
                "updated_at_epoch"
            )
            or 0
        )

    except Exception:

        heartbeat_ts = 0.0

    if (
        heartbeat_ts <= 0
        or now - heartbeat_ts > 180
    ):

        return {
            "pass": False,
            "reason":
                "COLLECTOR_DOWN_OR_STALE",
            "details": [],
        }

    required_columns = {
        "open",
        "high",
        "low",
        "close",
        "volume",
        "is_closed",
    }

    details = []

    for sym in sorted(
        {
            str(x).upper()
            for x in symbols
        }
    ):

        path = next(
            (
                p
                for p in paths(sym, tf)
                if p.is_file()
            ),
            None,
        )

        if path is None:

            return {
                "pass": False,
                "reason":
                    "MISSING_SYMBOL:"
                    + sym,
                "details":
                    details,
            }

        frame = pd.read_parquet(
            path
        )

        missing = (
            required_columns
            - set(frame.columns)
        )

        if missing:

            return {
                "pass": False,
                "reason":
                    "MISSING_OHLCV:"
                    + sym,
                "details":
                    details,
            }

        closed = frame[
            frame["is_closed"].astype(
                bool
            )
        ].copy()

        if closed.empty:

            return {
                "pass": False,
                "reason":
                    "MISSING_CLOSED_BAR:"
                    + sym,
                "details":
                    details,
            }

        if "open_time" in closed.columns:

            latest = (
                epoch(
                    closed[
                        "open_time"
                    ].iloc[-1]
                )
                + seconds
            )

        elif "ts" in closed.columns:

            latest = epoch(
                closed[
                    "ts"
                ].iloc[-1]
            )

        else:

            return {
                "pass": False,
                "reason":
                    "MISSING_TIMESTAMP:"
                    + sym,
                "details":
                    details,
            }

        age = now - latest

        valid = (
            math.isfinite(latest)
            and latest <= now + 90
            and age >= -90
            and age <= max_age
        )

        details.append(
            {
                "symbol": sym,
                "pass": bool(valid),
                "latest_ts":
                    latest,
                "age_seconds":
                    age,
            }
        )

        if not valid:

            return {
                "pass": False,
                "reason":
                    "STALE_CLOSED_BAR:"
                    + sym,
                "details":
                    details,
            }

    return {
        "pass": True,
        "reason": "",
        "required_symbols":
            sorted(
                {
                    str(x).upper()
                    for x in symbols
                }
            ),
        "details":
            details,
    }


if __name__ == "__main__":

    import argparse

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--timeframe",
        default="1H",
    )

    args = parser.parse_args()

    result = check_candle_freshness(
        args.timeframe
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
        )
    )

    raise SystemExit(
        0
        if result.get("pass")
        is True
        else 2
    )