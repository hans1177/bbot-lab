from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


ROOT = Path("/home/ec2-user/bot")
DATA = ROOT / "backtest_data"

SCHEMA = "B_BOT_V3_SERVER_CANDLE_WATCHDOG_V1"

DEFAULT_SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "TRUMPUSDT",
    "JTOUSDT",
)

# We intentionally supervise using 1H.
# Long TFs should not be used as liveness probes.
MAX_1H_AGE_SECONDS = 3 * 3600

# Prevent restart storms.
RESTART_COOLDOWN_SECONDS = 30 * 60

STATE_FILE = (
    ROOT
    / "state"
    / "server_candle_watchdog_state.json"
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value):
    if value is None:
        return None

    try:
        if isinstance(value, (int, float)):
            x = float(value)

            if x > 1e12:
                return pd.to_datetime(
                    x,
                    unit="ms",
                    utc=True,
                )

            if x > 1e9:
                return pd.to_datetime(
                    x,
                    unit="s",
                    utc=True,
                )

        x = pd.to_datetime(
            value,
            utc=True,
            errors="coerce",
        )

        if pd.isna(x):
            return None

        return x

    except Exception:
        return None


def read_state() -> dict:
    if not STATE_FILE.exists():
        return {}

    try:
        obj = json.loads(
            STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

        return (
            obj
            if isinstance(obj, dict)
            else {}
        )

    except Exception:
        return {}


def atomic_write_state(obj: dict) -> None:
    STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp = STATE_FILE.with_name(
        STATE_FILE.name + ".tmp"
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
    )

    tmp.replace(STATE_FILE)


def inspect_symbol(symbol: str) -> dict:
    path = (
        DATA
        / symbol
        / "1h.parquet"
    )

    result = {
        "symbol": symbol,
        "path": str(path),
        "pass": False,
        "reason": None,
        "open_time": None,
        "age_seconds": None,
    }

    if not path.exists():
        result["reason"] = "MISSING"
        return result

    try:
        df = pd.read_parquet(path)
    except Exception as exc:
        result["reason"] = (
            "READ_FAIL:"
            + type(exc).__name__
        )
        return result

    if df.empty:
        result["reason"] = "EMPTY"
        return result

    if "is_closed" not in df.columns:
        result["reason"] = "IS_CLOSED_MISSING"
        return result

    closed = df[
        df["is_closed"].astype(bool)
    ]

    if closed.empty:
        result["reason"] = "NO_CLOSED_BAR"
        return result

    row = closed.iloc[-1]

    ts = parse_timestamp(
        row.get("open_time")
    )

    if ts is None:
        result["reason"] = "BAD_OPEN_TIME"
        return result

    now = pd.Timestamp.now(tz="UTC")

    age = (
        now - ts
    ).total_seconds()

    result["open_time"] = (
        ts.isoformat()
    )

    result["age_seconds"] = (
        float(age)
    )

    if (
        age < 0
        or age > MAX_1H_AGE_SECONDS
    ):
        result["reason"] = "STALE"
        return result

    result["pass"] = True
    result["reason"] = "FRESH"

    return result


def collector_active() -> bool:
    p = subprocess.run(
        [
            "systemctl",
            "is-active",
            "--quiet",
            "collector.service",
        ],
        check=False,
    )

    return p.returncode == 0


def restart_collector() -> int:
    p = subprocess.run(
        [
            "sudo",
            "systemctl",
            "restart",
            "collector.service",
        ],
        check=False,
    )

    return int(p.returncode)


def run_once(
    *,
    dry_run: bool,
    symbols: tuple[str, ...],
) -> int:

    now = time.time()

    details = [
        inspect_symbol(x)
        for x in symbols
    ]

    fresh_count = sum(
        1
        for x in details
        if x["pass"]
    )

    # Require majority failure.
    # One bad symbol must never restart the feed.
    stale_majority = (
        fresh_count
        < (
            len(details) // 2 + 1
        )
    )

    active = collector_active()

    state = read_state()

    last_restart = float(
        state.get(
            "last_restart_epoch"
        )
        or 0
    )

    cooldown_ok = (
        now - last_restart
        >= RESTART_COOLDOWN_SECONDS
    )

    reason = "HEALTHY"

    restart_needed = False

    if not active:
        reason = "COLLECTOR_NOT_ACTIVE"
        restart_needed = True

    elif stale_majority:
        reason = "MAJORITY_1H_STALE"
        restart_needed = True

    out = {
        "schema": SCHEMA,
        "checked_at":
            utc_now().isoformat(),
        "collector_active": active,
        "fresh_count": fresh_count,
        "symbol_count": len(details),
        "stale_majority":
            stale_majority,
        "restart_needed":
            restart_needed,
        "cooldown_ok":
            cooldown_ok,
        "dry_run":
            dry_run,
        "reason":
            reason,
        "details":
            details,
    }

    print(
        json.dumps(
            out,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )

    if not restart_needed:
        return 0

    if dry_run:
        print(
            "WATCHDOG_ACTION="
            "DRY_RUN_WOULD_RESTART"
        )
        return 0

    if not cooldown_ok:
        print(
            "WATCHDOG_ACTION="
            "RESTART_SUPPRESSED_COOLDOWN"
        )
        return 0

    # Recheck after 20 seconds to reject
    # transient file/update timing.
    time.sleep(20)

    confirm = [
        inspect_symbol(x)
        for x in symbols
    ]

    confirm_fresh = sum(
        1
        for x in confirm
        if x["pass"]
    )

    confirm_stale_majority = (
        confirm_fresh
        < (
            len(confirm) // 2 + 1
        )
    )

    if (
        collector_active()
        and not confirm_stale_majority
    ):
        print(
            "WATCHDOG_ACTION="
            "RECOVERED_BEFORE_RESTART"
        )
        return 0

    rc = restart_collector()

    state.update(
        {
            "schema": SCHEMA,
            "last_restart_epoch":
                time.time(),
            "last_restart_reason":
                reason,
            "last_restart_rc":
                rc,
        }
    )

    atomic_write_state(state)

    print(
        "WATCHDOG_ACTION="
        "COLLECTOR_RESTART"
    )

    print(
        "WATCHDOG_RESTART_RC="
        + str(rc)
    )

    return rc


def main() -> int:
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--dry-run",
        action="store_true",
    )

    ap.add_argument(
        "--symbols",
        default=",".join(
            DEFAULT_SYMBOLS
        ),
    )

    args = ap.parse_args()

    symbols = tuple(
        x.strip().upper()
        for x in args.symbols.split(",")
        if x.strip()
    )

    if len(symbols) < 3:
        raise SystemExit(
            "AT_LEAST_3_SYMBOLS_REQUIRED"
        )

    return run_once(
        dry_run=bool(args.dry_run),
        symbols=symbols,
    )


if __name__ == "__main__":
    raise SystemExit(main())
