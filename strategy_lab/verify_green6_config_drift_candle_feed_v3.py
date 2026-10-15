from pathlib import Path
import hashlib
import subprocess


ROOT = Path(r"C:\my\bottest")

SYNC = ROOT / "strategy_runtime_sync.ps1"
COLLECTOR = ROOT / "collector.py"

MERGER = (
    ROOT
    / "strategy_lab"
    / "v3_runtime_config_merge.py"
)

SUPERVISOR = (
    ROOT
    / "strategy_lab"
    / "v3_candle_feed_supervisor.py"
)

UNIVERSE = (
    ROOT
    / "strategy_lab"
    / "v3_collector_required_universe.py"
)

FRESHNESS = (
    ROOT
    / "strategy_lab"
    / "v3_candle_freshness_gate.py"
)

SHADOW = (
    ROOT
    / "strategy_lab"
    / "v3_shadow_engine.py"
)

FROZEN = {
    "order.py":
        "513e85ab9935e8ea107f5f26dcb359f382c8deddc63c90d8bcf80348e45ec86c",

    "runner.py":
        "4926850cabce4d2659d6863032ac9630aaf30c9e074483cc5f38457ab35bc160",

    "risk_engine.py":
        "768aaa01735b82ef2705a186f244fbe3064954888d53f3546b09d05d49280fa6",

    "candidate.py":
        "b33cb689602f9dd2c1287ce6ce78c175fb38b4f7c90d9d5d695a34e721d423ac",
}


def read(path):
    if not path.is_file():
        return ""

    return path.read_text(
        encoding="utf-8-sig",
        errors="replace",
    )


def compact(value):
    return "".join(
        value.split()
    ).lower()


def sha(path):
    if not path.is_file():
        return ""

    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


rows = []


def check(name, ok, detail=""):
    rows.append({
        "name": name,
        "ok": bool(ok),
        "detail": str(detail or ""),
    })


sync = read(SYNC)
collector = read(COLLECTOR)
merger = read(MERGER)
supervisor = read(SUPERVISOR)
universe = read(UNIVERSE)
freshness = read(FRESHNESS)
shadow = read(SHADOW)

sync_c = compact(sync)
collector_c = compact(collector)
merger_c = compact(merger)
supervisor_c = compact(supervisor)
universe_c = compact(universe)
freshness_c = compact(freshness)
shadow_c = compact(shadow)


# ============================================================
# FOUNDATION
# ============================================================

check(
    "RUNTIME_SYNC_EXISTS",
    SYNC.is_file(),
)

check(
    "COLLECTOR_EXISTS",
    COLLECTOR.is_file(),
)

check(
    "COLLECTOR_HAS_BINANCE_KLINE",
    "futures_klines"
    in collector,
)

check(
    "COLLECTOR_HAS_PERSISTENT_MAIN_LOOP",
    (
        'if __name__ == "__main__"'
        in collector
        and
        "while True"
        in collector
    ),
)

check(
    "COLLECTOR_WRITES_CANDLES",
    (
        "save_rows"
        in collector
        and
        "rest_sync"
        in collector
    ),
)


# ============================================================
# GREEN-6B
# FRAME-SCOPED STRATEGY CONFIG APPLY
# ============================================================

check(
    "FRAME_SCOPED_MERGER_EXISTS",
    MERGER.is_file(),
)

check(
    "MERGER_DECLARES_FRAME_SCOPED_APPLY",
    "FRAME_SCOPED_STRATEGY_APPLY"
    in merger,
)

check(
    "MERGER_READS_ACTIVE_CONFIG",
    "strategy_config.json"
    in merger,
)

check(
    "MERGER_READS_PENDING_CONFIG",
    (
        "pending"
        in merger_c
        or
        "strategy_config_file"
        in merger_c
    ),
)

check(
    "MERGER_USES_REQUEST_TIMEFRAME",
    "timeframe"
    in merger_c,
)

check(
    "MERGER_PRESERVES_OTHER_FRAMES",
    (
        "preserve_other_frames"
        in merger_c
        or
        "non_target_frames_unchanged"
        in merger_c
    ),
)

check(
    "MERGER_TARGET_FRAME_ONLY",
    (
        "frames"
        in merger_c
        and
        "timeframe"
        in merger_c
    ),
)

check(
    "MERGER_FAILS_ON_TARGET_BINDING_ERROR",
    (
        "strategy_version"
        in merger_c
        and
        "strategy"
        in merger_c
        and
        "fail"
        in merger_c
    ),
)

check(
    "SYNC_CALLS_FRAME_SCOPED_MERGER",
    "v3_runtime_config_merge.py"
    in sync,
)

check(
    "SYNC_NO_LONGER_BLINDLY_REPLACES_PENDING_CONFIG",
    (
        'copy-item$cfg$tmp-force;move-item$tmp"$root\\strategy_config.json"-force'
        not in sync_c
    ),
)


# ============================================================
# POLICY SAFETY
# ============================================================

check(
    "MERGER_BRANCHES_VERSION_KIND",
    (
        "version_kind"
        in merger_c
        and
        "strategy"
        in merger_c
        and
        "policy"
        in merger_c
    ),
)

check(
    "POLICY_PATH_FAIL_CLOSED_OR_EXACT",
    (
        "policy_exact_apply"
        in merger_c
        or
        "policy_merge"
        in merger_c
        or
        "policy_unsupported_fail_closed"
        in merger_c
    ),
)


# ============================================================
# GREEN-6A-1
# REQUIRED COLLECTOR UNIVERSE
# ============================================================

check(
    "REQUIRED_UNIVERSE_RESOLVER_EXISTS",
    UNIVERSE.is_file(),
)

check(
    "UNIVERSE_READS_CONFIG_JSON",
    "config.json"
    in universe,
)

check(
    "UNIVERSE_READS_ACTIVE_STRATEGY_CONFIG",
    "strategy_config.json"
    in universe,
)

check(
    "UNIVERSE_READS_DEPLOYMENT_STATE",
    "deployment_state.json"
    in universe,
)

check(
    "UNIVERSE_READS_SHADOW_VALIDATION",
    (
        "shadow.json"
        in universe
        or
        "validation"
        in universe_c
    ),
)

check(
    "UNIVERSE_HAS_CONFIG_ENABLED_SYMBOLS",
    (
        "CONFIG_ENABLED_SYMBOLS"
        in universe
        or
        "config_enabled_symbols"
        in universe
    ),
)

check(
    "UNIVERSE_HAS_ACTIVE_STRATEGY_SYMBOLS",
    (
        "ACTIVE_STRATEGY_SYMBOLS"
        in universe
        or
        "active_strategy_symbols"
        in universe
    ),
)

check(
    "UNIVERSE_HAS_SHADOW_REQUIRED_SYMBOLS",
    (
        "SHADOW_REQUIRED_SYMBOLS"
        in universe
        or
        "shadow_required_symbols"
        in universe
    ),
)

check(
    "UNIVERSE_DECLARES_REQUIRED_UNION",
    (
        "COLLECTOR_REQUIRED_UNIVERSE"
        in universe
        or
        "required_universe"
        in universe_c
    ),
)

check(
    "UNIVERSE_DEDUPLICATES_SYMBOLS",
    (
        "set("
        in universe
        or
        "sorted(set"
        in universe_c
    ),
)

check(
    "UNIVERSE_WRITES_EVIDENCE_FILE",
    (
        "collector_required_universe.json"
        in universe
    ),
)

check(
    "UNIVERSE_NO_ORDER_CALLS",
    (
        "futures_create_order"
        not in universe_c
        and
        "create_order("
        not in universe_c
        and
        "futures_cancel_order"
        not in universe_c
    ),
)


# ============================================================
# GREEN-6A-2
# COLLECTOR MUST CONSUME REQUIRED UNIVERSE
# ============================================================

check(
    "COLLECTOR_READS_REQUIRED_UNIVERSE",
    (
        "collector_required_universe.json"
        in collector
        or
        "v3_collector_required_universe"
        in collector
    ),
)

check(
    "COLLECTOR_UNIONS_CONFIG_AND_REQUIRED_SYMBOLS",
    (
        "required_universe"
        in collector_c
        or
        "required_symbols"
        in collector_c
    ),
)

check(
    "COLLECTOR_RELOADS_REQUIRED_UNIVERSE",
    (
        "required_universe"
        in collector_c
        and
        (
            "mtime"
            in collector_c
            or
            "reload"
            in collector_c
        )
    ),
)


# ============================================================
# GREEN-6A-3
# AUTONOMOUS SUPERVISOR
# ============================================================

check(
    "CANDLE_SUPERVISOR_EXISTS",
    SUPERVISOR.is_file(),
)

check(
    "SUPERVISOR_REFERENCES_COLLECTOR",
    "collector.py"
    in supervisor,
)

check(
    "SUPERVISOR_USES_REQUIRED_UNIVERSE",
    (
        "v3_collector_required_universe"
        in supervisor
        or
        "collector_required_universe.json"
        in supervisor
    ),
)

check(
    "SUPERVISOR_USES_PYTHONW",
    (
        "pythonw"
        in supervisor_c
        or
        "pythonw.exe"
        in supervisor_c
    ),
)

check(
    "SUPERVISOR_SINGLE_INSTANCE",
    (
        "collector"
        in supervisor_c
        and
        (
            "win32_process"
            in supervisor_c
            or
            "processid"
            in supervisor_c
            or
            "powershell"
            in supervisor_c
        )
    ),
)

check(
    "SUPERVISOR_NO_REAL_ORDER",
    (
        "futures_create_order"
        not in supervisor_c
        and
        "create_order("
        not in supervisor_c
        and
        "futures_cancel_order"
        not in supervisor_c
    ),
)

check(
    "COLLECTOR_NO_DIRECT_ORDER_CALL",
    (
        "client.futures_create_order"
        not in collector_c
        and
        "client.create_order"
        not in collector_c
        and
        "client.futures_cancel_order"
        not in collector_c
    ),
)


# ============================================================
# GREEN-6A-4
# FRESH CLOSED-BAR SAFETY GATE
# ============================================================

check(
    "CANDLE_FRESHNESS_GATE_EXISTS",
    FRESHNESS.is_file(),
)

check(
    "FRESHNESS_GATE_READS_REQUIRED_UNIVERSE",
    (
        "collector_required_universe.json"
        in freshness
        or
        "v3_collector_required_universe"
        in freshness
    ),
)

check(
    "FRESHNESS_GATE_REQUIRES_OHLCV",
    all(
        key in freshness_c
        for key in (
            "open",
            "high",
            "low",
            "close",
            "volume",
        )
    ),
)

check(
    "FRESHNESS_GATE_CLOSED_BAR_ONLY",
    (
        "is_closed"
        in freshness_c
        or
        "closed_bar"
        in freshness_c
    ),
)

check(
    "FRESHNESS_GATE_STALE_FAIL_CLOSED",
    (
        "stale"
        in freshness_c
        and
        (
            "fail"
            in freshness_c
            or
            "wait"
            in freshness_c
        )
    ),
)

check(
    "FRESHNESS_GATE_MISSING_SYMBOL_FAIL_CLOSED",
    (
        "missing"
        in freshness_c
        and
        "symbol"
        in freshness_c
    ),
)

check(
    "FRESHNESS_GATE_COLLECTOR_DOWN_FAIL_CLOSED",
    (
        "collector"
        in freshness_c
        and
        (
            "down"
            in freshness_c
            or
            "running"
            in freshness_c
        )
    ),
)

check(
    "FRESHNESS_GATE_NO_PROFITABILITY_LOGIC",
    (
        "profit"
        not in freshness_c
        and
        "win_rate"
        not in freshness_c
        and
        "mdd"
        not in freshness_c
    ),
)

check(
    "SHADOW_CALLS_CANDLE_FRESHNESS_GATE",
    (
        "v3_candle_freshness_gate"
        in shadow
        or
        "candle_freshness_gate"
        in shadow_c
    ),
)

check(
    "SHADOW_REQUIRED_UNIVERSE_WAIT_MARKER",
    (
        "REQUIRED_UNIVERSE"
        in shadow
        or
        "required_universe"
        in shadow_c
    ),
)


# ============================================================
# TASK
# ============================================================

task_found = False
task_detail = ""

try:

    result = subprocess.run(
        [
            "schtasks.exe",
            "/Query",
            "/TN",
            "BBot_Local_Candle_Collector",
            "/FO",
            "LIST",
            "/V",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        encoding="mbcs",
        errors="replace",
    )

    task_found = (
        result.returncode == 0
    )

    if task_found:
        task_detail = result.stdout[-700:]
    else:
        task_detail = result.stderr[-700:]

except Exception as exc:
    task_detail = repr(exc)


check(
    "CANDLE_SUPERVISOR_TASK_EXISTS",
    task_found,
    task_detail,
)


# ============================================================
# FROZEN CORE
# ============================================================

for name, expected in FROZEN.items():

    got = sha(
        ROOT / name
    )

    check(
        "FROZEN_"
        + name.upper(),
        got == expected,
        got,
    )


# ============================================================
# REPORT
# ============================================================

failed = [
    row
    for row in rows
    if not row["ok"]
]

print("=" * 104)
print("B-BOT GREEN-6 V2 CONFIG DRIFT + REQUIRED UNIVERSE + CANDLE FEED")
print("=" * 104)

for row in rows:

    status = (
        "PASS"
        if row["ok"]
        else "FAIL"
    )

    detail = ""

    if row["detail"]:
        detail = (
            " :: "
            + row["detail"]
        )

    print(
        f"{status:4} "
        f"{row['name']}"
        f"{detail}"
    )

print()
print(
    "TOTAL_CHECKS="
    + str(len(rows))
)

print(
    "FAILED_COUNT="
    + str(len(failed))
)

if failed:

    print(
        "GREEN6_V2_STATUS=RED"
    )

    print(
        "RED_GAPS="
        + ",".join(
            row["name"]
            for row in failed
        )
    )

    raise SystemExit(2)

print(
    "GREEN6_V2_STATUS=GREEN"
)

raise SystemExit(0)
