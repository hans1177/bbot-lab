# standard_v2_candidate_parity_check.py
# LOCAL-ONLY validation for STANDARD V2 server candidate files.
# Does not modify server files and does not deploy/restart anything.

from __future__ import annotations

import hashlib
import json
import py_compile
from pathlib import Path


ROOT = Path(__file__).resolve().parent

RUNNER = ROOT / "runner_STANDARD_V2_candidate.py"
STRATEGY = ROOT / "strategy_config_STANDARD_V2_candidate.json"

EXPECTED_RUNNER_SHA256 = (
    "f3a413cf4539d65de791ea07f48dfecc2c249bc412d8f54f283a5b1b64c234ae"
)
EXPECTED_STRATEGY_SHA256 = (
    "7c7df13785243aa589b686f23281014030b0816445cfc9fdd8836abd703e635a"
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def require(cond: bool, name: str) -> None:
    print(f"{name} =", "PASS" if cond else "FAIL")
    if not cond:
        raise SystemExit(2)


def main() -> None:
    print("=" * 88)
    print("STANDARD V2 LOCAL CANDIDATE PARITY CHECK")
    print("=" * 88)

    require(RUNNER.exists(), "RUNNER FILE EXISTS")
    require(STRATEGY.exists(), "STRATEGY CONFIG EXISTS")

    runner_hash = sha256(RUNNER)
    strategy_hash = sha256(STRATEGY)

    print("RUNNER SHA256   =", runner_hash)
    print("STRATEGY SHA256 =", strategy_hash)

    require(
        runner_hash == EXPECTED_RUNNER_SHA256,
        "RUNNER HASH",
    )
    require(
        strategy_hash == EXPECTED_STRATEGY_SHA256,
        "STRATEGY HASH",
    )

    # Syntax check only. Does not import or run the runner.
    py_compile.compile(str(RUNNER), doraise=True)
    print("RUNNER PY_COMPILE = PASS")

    runner_text = RUNNER.read_text(encoding="utf-8")

    require(
        "TRAIL_START_PCT = 3.0" in runner_text
        and "TRAIL_CALLBACK_PCT = 1.5" in runner_text,
        "TRAILING 3 / 1.5",
    )
    require(
        "DEFAULT_UNIVERSE_TOP_N = 30" in runner_text,
        "DEFAULT TOP N = 30",
    )
    require(
        "def strategy_universe_top_n():" in runner_text,
        "TOP-N CONFIG LOADER",
    )
    require(
        "reason=OUTSIDE_DYNAMIC_TOPN" in runner_text,
        "TOP-N NEW ENTRY BLOCK",
    )
    require(
        "managed_only = sorted(" in runner_text,
        "OUTSIDE TOP-N POSITION MANAGEMENT",
    )
    require(
        "def opposite_signal_state_wait(" in runner_text
        and "REVERSE_STATE_WAIT" in runner_text,
        "OPPOSITE NEXT 1H STATE WAIT",
    )
    require(
        "def control_sl_once():" in runner_text,
        "CONTROL SL PRESERVED",
    )
    require(
        "NEXT_1H_NOT_READY" in runner_text
        and "CURRENT_STATE_REENTRY" in runner_text,
        "STATE REENTRY PRESERVED",
    )
    require(
        "risk_engine.entry_gate(" in runner_text,
        "CROSS ENTRY GATE PRESERVED",
    )

    cfg = json.loads(STRATEGY.read_text(encoding="utf-8"))

    require(cfg.get("universe_top_n") == 30, "CONFIG UNIVERSE TOP N = 30")

    frames = cfg.get("frames") or {}

    require(
        frames.get("12H", {}).get("enabled") is True
        and frames.get("12H", {}).get("strategies") == ["EMA_CROSS", "SAR"]
        and frames.get("12H", {}).get("leverage") == 10,
        "12H EMA_CROSS + SAR / 10x",
    )

    require(
        frames.get("1D", {}).get("enabled") is True
        and frames.get("1D", {}).get("strategies") == ["EMA_CROSS", "SAR"]
        and frames.get("1D", {}).get("leverage") == 15,
        "1D EMA_CROSS + SAR / 15x",
    )

    require(
        all(
            frames.get(tf, {}).get("enabled") is False
            for tf in ("1H", "6H", "1W", "1M")
        ),
        "OTHER TF OFF",
    )

    lev = cfg.get("leverage_policy") or {}

    require(lev.get("absolute_max") == 15, "ABSOLUTE MAX LEVERAGE = 15")
    require(
        lev.get("server_dynamic_leverage") is False,
        "DYNAMIC LEVERAGE OFF",
    )

    auto = cfg.get("auto_promotion") or {}
    require(auto.get("enabled") is False, "AUTO PROMOTION OFF")

    print()
    print("=" * 88)
    print("LOCAL CANDIDATE PARITY = PASS")
    print("NEXT = SERVER READ-ONLY HASH/PARITY CHECK")
    print("NO SERVER FILES MODIFIED")
    print("=" * 88)


if __name__ == "__main__":
    main()
