# -*- coding: utf-8 -*-
# 파일명: strategy_lab/v3_shadow_engine.py
"""
B-Bot V3 minimal Shadow runtime-safety evidence producer.

Evidence only:
- no live execution
- no server mutation
- no deployment request creation
- fail closed when fresh closed-bar evidence is unavailable
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(
    os.getenv(
        "BBOT_LOCAL_ROOT",
        r"C:\my\bottest",
    )
)

LAB = ROOT / "strategy_lab"
RES = LAB / "results" / "v3"

STATE = (
    RES
    / "deployment_state.json"
)

VERSION_REGISTRY = (
    RES
    / "version_registry.json"
)

CACHE = (
    ROOT
    / "gate_lab_exact_long_cache.parquet"
)

SCHEMA = (
    "B_BOT_V3_MINIMAL_SHADOW_EVIDENCE_V1"
)

ROLE = "LOCAL_RESEARCH"

TF_SECONDS = {
    "30M": 1800,
    "1H": 3600,
    "6H": 21600,
    "12H": 43200,
    "1D": 86400,
    "1W": 604800,
}


def now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def load_json(
    path,
    default=None,
):

    if default is None:
        default = {}

    if not path.is_file():
        return default

    try:
        value = json.loads(
            path.read_text(
                encoding="utf-8-sig"
            )
        )
    except Exception:
        return default

    return value


def atomic_json(
    path,
    value,
):

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp = path.with_name(
        path.name
        + f".{os.getpid()}.tmp"
    )

    temp.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )

    os.replace(
        temp,
        path,
    )


def sha256(path):

    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def _normalize_rel(value):

    return (
        str(value or "")
        .replace("\\", "/")
        .lstrip("./")
    )


def _safe_root_file(raw):

    rel = _normalize_rel(
        raw
    )

    if not rel:
        return None

    path = (
        ROOT
        / rel
    ).resolve()

    try:
        path.relative_to(
            ROOT.resolve()
        )
    except Exception:
        return None

    if (
        not path.is_file()
        or path.suffix.lower()
        != ".py"
    ):
        return None

    return path


def _evidence_path(
    version_id,
    strategy_instance_id="",
):
    base = (
        RES
        / "validation"
        / str(version_id)
    )

    if str(strategy_instance_id or "").strip():
        return (
            base
            / (
                "shadow__"
                + str(strategy_instance_id).strip()
                + ".json"
            )
        )

    return base / "shadow.json"



def _version_binding(
    version_id,
    strategy,
    timeframe,
    source_rel,
    strategy_instance_id="",
):
    registry = load_json(
        VERSION_REGISTRY,
        {},
    )

    row = (
        (
            registry.get("strategy_versions")
            or {}
        ).get(str(version_id))
        or {}
    )

    if not isinstance(row, dict):
        return False

    if str(
        row.get("kind")
        or ""
    ).upper() != "STRATEGY":
        return False

    if str(
        row.get("status")
        or ""
    ).upper() not in {
        "VALIDATED",
        "PROVISIONAL",
        "CANARY",
        "SMALL",
        "NORMAL",
    }:
        return False

    payload = row.get("payload") or {}

    if str(
        payload.get("strategy")
        or ""
    ).upper() != str(
        strategy
        or ""
    ).upper():
        return False

    if str(
        payload.get("timeframe")
        or ""
    ).upper() != str(
        timeframe
        or ""
    ).upper():
        return False

    registered_source = _normalize_rel(
        payload.get("source_file")
    )
    requested_source = _normalize_rel(
        source_rel
    )

    if (
        requested_source
        and registered_source
        and registered_source != requested_source
    ):
        return False

    instance_id = str(
        strategy_instance_id
        or ""
    ).strip()

    if not instance_id:
        return False

    instance = (
        (
            registry.get("strategy_instances")
            or {}
        ).get(instance_id)
        or {}
    )

    if not isinstance(instance, dict):
        return False

    if str(
        instance.get("strategy_instance_id")
        or ""
    ) != instance_id:
        return False

    if str(
        instance.get("strategy_id")
        or ""
    ).upper() != str(
        strategy
        or ""
    ).upper():
        return False

    if str(
        instance.get("timeframe")
        or ""
    ).upper() != str(
        timeframe
        or ""
    ).upper():
        return False

    if str(
        instance.get("exact_strategy_version")
        or ""
    ) != str(version_id):
        return False

    if str(
        instance.get("status")
        or ""
    ).upper() != "LOCAL_SHADOW":
        return False

    instance_source = _normalize_rel(
        instance.get("source_file")
    )

    if (
        requested_source
        and instance_source
        and instance_source != requested_source
    ):
        return False

    return True



def _load_module(
    source,
    version_id,
):

    module_name = (
        "_bbot_shadow_"
        + hashlib.sha256(
            (
                str(version_id)
                + "|"
                + str(source)
            ).encode(
                "utf-8"
            )
        ).hexdigest()[:16]
    )

    spec = (
        importlib.util
        .spec_from_file_location(
            module_name,
            source,
        )
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise RuntimeError(
            "SHADOW_MODULE_SPEC_INVALID"
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

    if not callable(
        getattr(
            module,
            "generate_signals",
            None,
        )
    ):
        raise RuntimeError(
            "GENERATE_SIGNALS_MISSING"
        )

    if not callable(
        getattr(
            module,
            "signal",
            None,
        )
    ):
        raise RuntimeError(
            "LIVE_SIGNAL_WRAPPER_MISSING"
        )

    return module


def _alias_columns(df):

    out = df.copy()

    by_lower = {
        str(col).lower():
            col
        for col in out.columns
    }

    for key in (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "symbol",
        "ts",
    ):

        if (
            key not in out.columns
            and key in by_lower
        ):
            out[key] = out[
                by_lower[key]
            ]

    if (
        "ts" not in out.columns
        and "timestamp"
        in by_lower
    ):
        out["ts"] = out[
            by_lower["timestamp"]
        ]

    return out


def _prepare_frame(df):

    out = _alias_columns(
        df
    )

    required = (
        "open",
        "high",
        "low",
        "close",
        "volume",
    )

    if any(
        key not in out.columns
        for key in required
    ):
        return None

    for key in required:

        out[key] = pd.to_numeric(
            out[key],
            errors="coerce",
        )

    out = out.dropna(
        subset=list(required)
    )

    if "ts" in out.columns:

        if (
            pd.api.types
            .is_numeric_dtype(
                out["ts"]
            )
        ):

            out = out.sort_values(
                "ts"
            )

        else:

            parsed = pd.to_datetime(
                out["ts"],
                utc=True,
                errors="coerce",
            )

            out = (
                out.assign(
                    _shadow_ts_sort=parsed
                )
                .sort_values(
                    "_shadow_ts_sort"
                )
                .drop(
                    columns=[
                        "_shadow_ts_sort"
                    ]
                )
            )

    out = (
        out.tail(
            5000
        )
        .reset_index(
            drop=True
        )
    )

    if len(out) < 256:
        return None

    return out


def _market_frames(
    timeframe="1H",
):

    # REQUIRED_UNIVERSE
    # v3_candle_freshness_gate
    # fail closed / WAIT

    try:

        from v3_candle_freshness_gate import (
            check_candle_freshness,
        )

    except Exception as error:

        return (
            [],
            "REQUIRED_UNIVERSE_WAIT:"
            + type(error).__name__,
        )

    timeframe = str(
        timeframe or "1H"
    ).upper()

    freshness = (
        check_candle_freshness(
            timeframe
        )
    )

    if freshness.get(
        "pass"
    ) is not True:

        return (
            [],
            "REQUIRED_UNIVERSE_WAIT:"
            + str(
                freshness.get(
                    "reason"
                )
                or "FRESH_BAR_PENDING"
            ),
        )

    names = {
        "1H":
            ("1h.parquet", "1H.parquet"),

        "6H":
            ("6h.parquet", "6H.parquet"),

        "12H":
            ("12h.parquet", "12H.parquet"),

        "1D":
            ("1d.parquet", "1D.parquet"),

        "1W":
            ("1w.parquet", "1W.parquet"),
    }.get(
        timeframe,
        (
            timeframe.lower()
            + ".parquet",

            timeframe
            + ".parquet",
        ),
    )

    tf_seconds = (
        TF_SECONDS.get(
            timeframe
        )
        or 3600
    )

    root = (
        ROOT
        / "data"
        / "candles"
    )

    frames = []

    for symbol in (
        freshness.get(
            "required_symbols"
        )
        or []
    ):

        candidates = [
            root
            / str(symbol).upper()
            / name
            for name in names
        ]

        path = next(
            (
                p
                for p in candidates
                if p.is_file()
            ),
            None,
        )

        if path is None:

            return (
                [],
                "REQUIRED_UNIVERSE_WAIT:"
                "MISSING_SYMBOL",
            )

        raw = pd.read_parquet(
            path
        )

        if "is_closed" not in raw.columns:

            return (
                [],
                "REQUIRED_UNIVERSE_WAIT:"
                "CLOSED_BAR_FLAG_MISSING",
            )

        raw = raw[
            raw["is_closed"].astype(
                bool
            )
        ].copy()

        if raw.empty:

            return (
                [],
                "REQUIRED_UNIVERSE_WAIT:"
                "NO_CLOSED_BAR",
            )

        if (
            "ts" not in raw.columns
            and
            "open_time" in raw.columns
        ):

            opened = pd.to_datetime(
                raw["open_time"],
                utc=True,
                errors="coerce",
            )

            raw["ts"] = (
                opened.astype("int64")
                / 1_000_000_000
                + float(tf_seconds)
            )

        raw = _alias_columns(
            raw
        )

        prepared = _prepare_frame(
            raw
        )

        if prepared is None:

            return (
                [],
                "REQUIRED_UNIVERSE_WAIT:"
                "NO_USABLE_OHLCV",
            )

        frames.append(
            (
                str(symbol).upper(),
                prepared,
            )
        )

        if len(frames) >= 3:
            break

    if not frames:

        return (
            [],
            "REQUIRED_UNIVERSE_WAIT:"
            "NO_MARKET_FRAME",
        )

    return frames, ""


def _synthetic_frames():

    frames = []

    n = 2400

    end = (
        int(time.time())
        - 3600
    )

    ts = (
        np.arange(
            n,
            dtype=np.int64,
        )
        * 3600
        + (
            end
            - (n - 1) * 3600
        )
    )

    x = np.arange(
        n,
        dtype=float,
    )

    patterns = (
        (
            "SYNTH_TREND",

            100.0
            + 0.025 * x
            + 3.5 * np.sin(
                x / 18.0
            ),
        ),

        (
            "SYNTH_CYCLE",

            120.0
            + 12.0 * np.sin(
                x / 26.0
            )
            + 2.0 * np.sin(
                x / 5.0
            ),
        ),

        (
            "SYNTH_REGIME",

            90.0
            + np.where(
                x < n / 2,
                0.035 * x,
                0.035 * (n - x),
            )
            + 4.0 * np.sin(
                x / 11.0
            ),
        ),
    )

    for name, close in patterns:

        open_ = np.concatenate(
            (
                [close[0]],
                close[:-1],
            )
        )

        spread = (
            0.5
            + np.abs(
                np.sin(
                    x / 7.0
                )
            )
        )

        high = (
            np.maximum(
                open_,
                close,
            )
            + spread
        )

        low = (
            np.minimum(
                open_,
                close,
            )
            - spread
        )

        volume = (
            1000.0
            + 200.0
            * (
                1.0
                + np.sin(
                    x / 13.0
                )
            )
        )

        frame = pd.DataFrame(
            {
                "ts":
                    ts,

                "open":
                    open_,

                "high":
                    high,

                "low":
                    low,

                "close":
                    close,

                "volume":
                    volume,

                "symbol":
                    name,
            }
        )

        frames.append(
            (
                name,
                frame,
            )
        )

    return frames


def _signal_array(
    module,
    df,
    timeframe,
):

    raw = module.generate_signals(
        df.copy(),
        timeframe,
    )

    arr = np.asarray(
        raw
    )

    if len(arr) != len(df):

        raise RuntimeError(
            "SIGNAL_LENGTH_MISMATCH"
        )

    try:

        numeric = arr.astype(
            float
        )

    except Exception as error:

        raise RuntimeError(
            "SIGNAL_NOT_NUMERIC"
        ) from error

    if np.isinf(
        numeric
    ).any():

        raise RuntimeError(
            "SIGNAL_INF"
        )

    numeric = np.nan_to_num(
        numeric,
        nan=0.0,
        posinf=0.0,
        neginf=0.0,
    )

    return np.sign(
        numeric
    ).astype(
        np.int8
    )


def _prefix_invariant(
    module,
    df,
    timeframe,
):

    full = _signal_array(
        module,
        df,
        timeframe,
    )

    for ratio in (
        0.60,
        0.80,
        0.90,
    ):

        cut = max(
            128,
            int(
                len(df)
                * ratio
            ),
        )

        if cut >= len(df):
            continue

        prefix = (
            _signal_array(
                module,
                df.iloc[
                    :cut
                ].copy(),
                timeframe,
            )
        )

        if not np.array_equal(
            full[:cut],
            prefix,
        ):

            return False

    return True


def _signal_value(value):

    if isinstance(
        value,
        dict,
    ):

        value = (
            value.get(
                "signal"
            )
            if value.get(
                "signal"
            )
            is not None
            else value.get(
                "state"
            )
        )

    if isinstance(
        value,
        str,
    ):

        text = (
            value.strip().lower()
        )

        if text in {
            "long",
            "buy",
            "1",
        }:
            return 1

        if text in {
            "short",
            "sell",
            "-1",
        }:
            return -1

        if text in {
            "",
            "wait",
            "flat",
            "none",
            "neutral",
            "0",
        }:
            return 0

        raise RuntimeError(
            "LIVE_SIGNAL_TEXT_INVALID="
            + text
        )

    try:

        number = float(
            value
        )

    except Exception as error:

        raise RuntimeError(
            "LIVE_SIGNAL_VALUE_INVALID"
        ) from error

    if not math.isfinite(
        number
    ):

        raise RuntimeError(
            "LIVE_SIGNAL_VALUE_NONFINITE"
        )

    return (
        1
        if number > 0
        else
        -1
        if number < 0
        else 0
    )


def _wrapper_parity(
    module,
    df,
):

    live = _signal_array(
        module,
        df,
        "LIVE",
    )

    try:

        result = module.signal(
            df.copy(),
            params=None,
        )

    except TypeError:

        result = module.signal(
            df.copy()
        )

    actual = _signal_value(
        result
    )

    expected = (
        int(
            live[-1]
        )
        if len(live)
        else 0
    )

    return (
        actual
        == expected
    )


def _dryrun_transition(
    signals,
):

    arr = np.asarray(
        signals,
        dtype=np.int8,
    )

    for index in np.flatnonzero(
        arr != 0
    ):

        index = int(
            index
        )

        if (
            index + 3
            >= len(arr)
        ):
            continue

        side = int(
            arr[index]
        )

        events = [
            (
                "ENTRY",
                side,
                index,
            ),

            (
                "HOLD",
                side,
                index + 1,
            ),

            (
                "HOLD",
                side,
                index + 2,
            ),

            (
                "EXIT",
                0,
                index + 3,
            ),
        ]

        for later in range(
            index + 4,
            len(arr),
        ):

            if int(
                arr[later]
            ) != 0:

                events.append(
                    (
                        "REENTRY",
                        int(
                            arr[later]
                        ),
                        later,
                    )
                )

                break

        return (
            True,
            events,
        )

    return False, []


def _epoch_seconds(value):

    if isinstance(
        value,
        pd.Timestamp,
    ):

        if value.tzinfo is None:

            value = value.tz_localize(
                "UTC"
            )

        return float(
            value.timestamp()
        )

    if isinstance(
        value,
        datetime,
    ):

        if value.tzinfo is None:

            value = value.replace(
                tzinfo=timezone.utc
            )

        return float(
            value.timestamp()
        )

    try:

        number = float(
            value
        )

        while number > 100000000000:

            number /= 1000.0

        return number

    except Exception:
        pass

    parsed = pd.to_datetime(
        value,
        utc=True,
        errors="coerce",
    )

    if pd.isna(
        parsed
    ):

        raise RuntimeError(
            "TIMESTAMP_INVALID"
        )

    return float(
        parsed.timestamp()
    )


def _closed_bar_fresh(
    df,
    timeframe,
):

    if (
        "ts" not in df.columns
        or len(df) < 2
    ):

        return (
            False,
            None,
            None,
        )

    try:

        latest = (
            _epoch_seconds(
                df["ts"].iloc[-1]
            )
        )

    except Exception:

        return (
            False,
            None,
            None,
        )

    now_ts = time.time()

    age = (
        now_ts
        - latest
    )

    tf_seconds = (
        TF_SECONDS.get(
            str(
                timeframe
            ).upper()
        )
        or 3600
    )

    max_age = max(
        6 * 3600,
        float(tf_seconds)
        * 2.2,
    )

    valid = (
        latest
        <= now_ts + 90
        and age >= -90
        and age <= max_age
    )

    return (
        bool(valid),
        float(latest),
        float(age),
    )


def _cost_sanity(
    request,
    timeframe,
):

    if not isinstance(
        request,
        dict,
    ):
        return False, {}

    if str(
        request.get(
            "required_pre_runtime_gate"
        )
        or ""
    ) != (
        "MINIMAL_SHADOW_RUNTIME_SAFETY"
    ):

        return False, {}

    if request.get(
        "autonomous_rule_passed"
    ) is not True:

        return False, {}

    if request.get(
        "core_mutation"
    ) is not False:

        return False, {}

    if str(
        request.get(
            "tier"
        )
        or ""
    ).upper() != "CANARY":

        return False, {}

    try:

        request_weight = float(
            request.get(
                "canary_capital_weight"
            )
        )

    except Exception:

        return False, {}

    if abs(
        request_weight
        - 0.20
    ) > 1e-12:

        return False, {}

    config_rel = _normalize_rel(
        request.get(
            "strategy_config_file"
        )
    )

    config_path = (
        ROOT
        / config_rel
    ).resolve()

    try:

        config_path.relative_to(
            ROOT.resolve()
        )

    except Exception:

        return False, {}

    config = load_json(
        config_path,
        {},
    )

    frame = (
        (
            config.get(
                "frames"
            )
            or {}
        ).get(
            str(
                timeframe
            ).upper()
        )
        or {}
    )

    try:

        frame_weight = float(
            frame.get(
                "capital_weight"
            )
        )

        leverage = float(
            frame.get(
                "leverage"
            )
        )

    except Exception:

        return False, {}

    if abs(
        frame_weight
        - 0.20
    ) > 1e-12:

        return False, {}

    if str(
        frame.get(
            "deployment_tier"
        )
        or ""
    ).upper() != "CANARY":

        return False, {}

    if (
        not math.isfinite(
            leverage
        )
        or leverage <= 0
        or leverage > 125
    ):

        return False, {}

    evaluation = (
        (
            request.get(
                "research_evidence"
            )
            or {}
        ).get(
            "evaluation"
        )
        or {}
    )

    try:

        cost_stress = float(
            evaluation.get(
                "walk_forward_cost_stress_2x_avg_net_pct"
            )
        )

    except Exception:

        return False, {}

    # Presence/finite check only.
    # Profitability is deliberately NOT a Shadow gate.
    if not math.isfinite(
        cost_stress
    ):

        return False, {}

    base_notional = (
        1000000.0
    )

    simulated_exposure = (
        base_notional
        * frame_weight
        * leverage
    )

    sane = (
        math.isfinite(simulated_exposure)
        and simulated_exposure > 0
        and 0.0 < frame_weight <= 1.0
        and math.isfinite(leverage)
        and 0.0 < leverage <= 125.0
    )

    return (
        bool(sane),
        {
            "capital_weight":
                frame_weight,

            "leverage":
                leverage,

            "simulated_base_notional":
                base_notional,

            "simulated_exposure":
                simulated_exposure,

            "cost_stress_metric_present":
                True,
        },
    )


def _bundle_version_from_snapshot(snapshot):
    payload = snapshot if isinstance(snapshot,dict) else {}
    raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str).encode("utf-8")
    return "PB_" + hashlib.sha256(raw).hexdigest()[:32]

def _legacy_frozen_baseline_policy_pass(registry, instance, snapshot, request, trailing, state_reentry):
    """Validate an existing CANARY's *frozen* baseline contract, never a new trial.

    This is not individual-trailing/reentry optimization credit.  It is a
    separate, fail-closed runtime-parity check for pre-NEW_MAIN instances.
    """
    req = request if isinstance(request, dict) else {}
    research = req.get("research_evidence") or {}
    if not isinstance(research, dict):
        return False
    if str(req.get("pipeline_contract") or research.get("pipeline_contract") or "") == "NEW_MAIN_LOCAL_SHADOW_V1":
        return False
    if isinstance(research.get("individual_trailing"), dict) and research["individual_trailing"]:
        return False
    if isinstance(research.get("individual_reentry"), dict) and research["individual_reentry"]:
        return False

    if not isinstance(instance, dict) or instance.get("status") != "CANARY":
        return False
    if instance.get("approved_for_server_release") is not True:
        return False
    if instance.get("canary_authorized") is not True:
        return False
    origin = instance.get("evidence") or {}
    if not isinstance(origin, dict):
        return False
    origin_name = origin.get("source")
    if origin_name == "R4_EXISTING_DEPLOYMENT_MIGRATION":
        pass
    elif origin_name == "V3_DEPLOYMENT_CREATE_PROVISIONAL":
        if origin.get("full_validation_pass") is not True or origin.get("sealed_blind_pass") is not True:
            return False
    else:
        return False
    expected_reasons = {
        "R4_EXISTING_CANARY_EXACT_INSTANCE_SHADOW_PASS",
        "MINIMAL_SHADOW_RUNTIME_GATE_PASS",
    }
    career = instance.get("career") or []
    if not isinstance(career, list) or not any(
        isinstance(event, dict)
        and event.get("event") == "CANARY"
        and isinstance(event.get("evidence"), dict)
        and event["evidence"].get("reason") in expected_reasons
        for event in career
    ):
        return False

    version_id = str(instance.get("exact_strategy_version") or "")
    versions = registry.get("strategy_versions") or {}
    version = versions.get(version_id) if isinstance(versions, dict) else None
    if not isinstance(version, dict) or version.get("status") != "CANARY":
        return False
    payload = version.get("payload") or {}
    if (not isinstance(payload, dict)
        or str(payload.get("strategy") or "").upper() != str(instance.get("strategy_id") or "").upper()
        or str(payload.get("timeframe") or "").upper() != str(instance.get("timeframe") or "").upper()
        or str(req.get("strategy_version") or req.get("version_id") or "") != version_id):
        return False

    # A partial individual policy must NEVER be silently treated as legacy.
    if (trailing.get("individual_strategy_binding") is not None
        or state_reentry.get("individual_strategy_binding") is not None
        or "individual_mode" in state_reentry
        or "trigger" in state_reentry):
        return False

    cfg = load_json(ROOT / "strategy_config.json", {})
    behavior = snapshot.get("behavior_policy") or {}
    if (not isinstance(cfg, dict) or not isinstance(behavior, dict)
        or not isinstance(trailing, dict) or not isinstance(state_reentry, dict)
        or not trailing or not state_reentry
        or behavior.get("trailing") != cfg.get("trailing")
        or behavior.get("state_reentry") != cfg.get("state_reentry")):
        return False
    try:
        start = float(trailing.get("start_pct"))
        callback = float(trailing.get("callback_pct"))
    except (TypeError, ValueError):
        return False
    return bool(
        math.isfinite(start) and math.isfinite(callback)
        and start > callback > 0
        and trailing.get("enabled") is True
        and trailing.get("dynamic_adjustment") is False
        and str(trailing.get("mode") or "").upper() == "FIXED_BASELINE"
        and state_reentry.get("enabled") is True
        and state_reentry.get("dynamic_policy") is False
        and str(state_reentry.get("mode") or "").upper() == "CURRENT_STATE"
        and str(state_reentry.get("next_check_tf") or "").upper() == "1H"
    )


def _strategy_instance_policy_snapshot_check(frame_state,request):
    fr=frame_state if isinstance(frame_state,dict) else {}
    req=request if isinstance(request,dict) else {}
    errors=[]
    registry=load_json(VERSION_REGISTRY,{})
    sid=str(fr.get("strategy_instance_id") or req.get("strategy_instance_id") or "").strip()
    instance=((registry.get("strategy_instances") or {}).get(sid) or {})
    if not isinstance(instance,dict) or not sid:
        return False,{"errors":["STRATEGY_INSTANCE_REGISTRY_MISSING"]}
    snapshot=req.get("strategy_policy_snapshot") if isinstance(req.get("strategy_policy_snapshot"),dict) else {}
    if not snapshot:
        errors.append("REQUEST_POLICY_SNAPSHOT_MISSING")
    if instance.get("policy_snapshot") != snapshot:
        errors.append("INSTANCE_REQUEST_POLICY_SNAPSHOT_MISMATCH")
    bundle=str(req.get("strategy_policy_bundle_version") or fr.get("strategy_policy_bundle_version") or "")
    if str(instance.get("strategy_policy_bundle_version") or "") != bundle:
        errors.append("INSTANCE_POLICY_BUNDLE_VERSION_MISMATCH")
    if snapshot and _bundle_version_from_snapshot(snapshot) != bundle:
        errors.append("POLICY_BUNDLE_HASH_MISMATCH")

    versions=snapshot.get("policy_versions") if isinstance(snapshot.get("policy_versions"),dict) else {}
    behavior=snapshot.get("behavior_policy") if isinstance(snapshot.get("behavior_policy"),dict) else {}
    trailing=behavior.get("trailing") if isinstance(behavior.get("trailing"),dict) else {}
    state_reentry=behavior.get("state_reentry") if isinstance(behavior.get("state_reentry"),dict) else {}
    research=req.get("research_evidence") if isinstance(req.get("research_evidence"),dict) else {}
    selected_trailing=research.get("individual_trailing") if isinstance(research.get("individual_trailing"),dict) else {}
    selected_reentry=research.get("individual_reentry") if isinstance(research.get("individual_reentry"),dict) else {}

    try:
        start=float(trailing.get("start_pct")); callback=float(trailing.get("callback_pct"))
        selected_start=float(selected_trailing.get("start_pct")); selected_callback=float(selected_trailing.get("callback_pct"))
        trailing_ok=(
            trailing.get("enabled",True) is True
            and trailing.get("dynamic_adjustment") is not True
            and start>0 and callback>0 and callback<start
            and abs(start-selected_start)<=1e-12
            and abs(callback-selected_callback)<=1e-12
            and trailing.get("individual_strategy_binding") is True
        )
    except Exception:
        trailing_ok=False
    # Existing frozen-baseline contracts are checked separately, not marked
    # as individual-policy optimizations.  New pipelines cannot use this path.
    legacy_baseline_ok = (
        not errors
        and _legacy_frozen_baseline_policy_pass(
            registry, instance, snapshot, req, trailing, state_reentry
        )
    )
    if not trailing_ok and not legacy_baseline_ok:
        errors.append("INDIVIDUAL_TRAILING_SNAPSHOT_BINDING_FAIL")

    mode=str(selected_reentry.get("mode") or "CURRENT_STATE_NEXT_1H").upper()
    reentry_ok=False
    if mode=="CURRENT_STATE_NEXT_1H":
        reentry_ok=(
            state_reentry.get("enabled") is True
            and str(state_reentry.get("mode") or "").upper()=="CURRENT_STATE"
            and str(state_reentry.get("next_check_tf") or "").upper()=="1H"
            and str(state_reentry.get("individual_mode") or "").upper()=="CURRENT_STATE_NEXT_1H"
            and str(state_reentry.get("trigger") or "").upper()=="TRAILING_STOP_ONLY"
            and state_reentry.get("same_direction_required") is True
            and state_reentry.get("neutral_state_keeps_pending") is True
            and state_reentry.get("opposite_state_cancels") is True
            and state_reentry.get("same_bar_immediate_reorder_forbidden") is True
        )
    if not reentry_ok and not legacy_baseline_ok:
        errors.append("INDIVIDUAL_REENTRY_SNAPSHOT_BINDING_FAIL")
    if not str(versions.get("EXIT") or "").strip():
        errors.append("EXIT_POLICY_VERSION_MISSING")
    if state_reentry.get("enabled") is True and not str(versions.get("REENTRY") or "").strip():
        errors.append("REENTRY_POLICY_VERSION_MISSING")
    return (not errors),{
        "pass":not errors,
        "errors":errors,
        "strategy_instance_id":sid,
        "policy_bundle_version":bundle,
        "trailing_binding_pass":trailing_ok,
        "reentry_binding_pass":reentry_ok,
        "policy_contract_mode":"LEGACY_FROZEN_BASELINE_V1" if legacy_baseline_ok else "INDIVIDUAL_STRATEGY_V1",
        "legacy_frozen_baseline_policy_pass":bool(legacy_baseline_ok),
        "selected_trailing":selected_trailing,
        "selected_reentry":selected_reentry,
    }

def _action_sequence_integrity(events):
    events=list(events or [])
    if not events:
        return False,{"reason":"NO_TRANSITION_EVENTS"}
    live=False; entries=0; exits=0; reentries=0; errors=[]
    for raw in events:
        if not isinstance(raw,(list,tuple)) or not raw:
            errors.append("MALFORMED_ACTION"); continue
        action=str(raw[0] or "").upper()
        if action=="ENTRY":
            if live: errors.append("DUPLICATE_ENTRY")
            live=True; entries+=1
        elif action=="HOLD":
            if not live: errors.append("HOLD_WITHOUT_ENTRY")
        elif action=="EXIT":
            if not live: errors.append("EXIT_WITHOUT_ENTRY")
            live=False; exits+=1
        elif action=="REENTRY":
            if live: errors.append("REENTRY_WHILE_LIVE")
            live=True; reentries+=1
        else:
            errors.append("UNKNOWN_ACTION:"+action)
    ok=(not errors and entries>=1 and exits>=1 and reentries<=1)
    return ok,{"entries":entries,"exits":exits,"reentries":reentries,"errors":errors}

def _portfolio_gate_integration_check(version_id):
    try:
        import v3_portfolio_allocation_gate as portfolio_gate
        safe={
            "account_mode":"BALANCED",
            "available_margin_pass":True,
            "entry_risk_safe_size_pass":True,
            "account_risk_budget_pass":True,
            "margin_safety_reserve_pass":True,
            "symbol_concentration_pass":True,
            "strategy_concentration_pass":True,
            "correlation_concentration_pass":True,
            "portfolio_total_exposure_pass":True,
        }
        allow=portfolio_gate.evaluate_portfolio_allocation({"ok":False},version_id,"CANARY",safe)
        blocked=dict(safe); blocked["available_margin_pass"]=False
        deny=portfolio_gate.evaluate_portfolio_allocation({"ok":False},version_id,"CANARY",blocked)
        ok=(allow.get("ok") is True and str(allow.get("decision") or "").upper()=="ALLOW" and deny.get("ok") is False and str(deny.get("decision") or "").upper()=="BLOCK")
        return ok,{"allow":allow,"block":deny}
    except Exception as error:
        return False,{"error":type(error).__name__+":"+str(error)[:300]}



# ============================================================
# RUNNER_TEST_STAGE2_COUNTERFACTUAL_V1
#
# TEST / research-SHADOW only.
# This has ZERO real-order authority.
#
# Formal minimal Shadow PASS logic below remains unchanged.
# ============================================================

def runner_test_stage2_counterfactual_policy():
    return {
        "schema":
            "B_BOT_RUNNER_TEST_STAGE2_COUNTERFACTUAL_V1",

        "status":
            "ACTIVE",

        "scope":[
            "LOCAL_TEST",
            "LOCAL_RESEARCH_SHADOW",
        ],

        "real_order_authority":
            False,

        "formal_minimal_shadow_pass_authority":
            False,

        "would_block_continue_reasons":[
            "INTRABAR_ORDER_AMBIGUOUS",
            "POSITION_NOT_ALIVE_AT_NOW",

            "CALC_QTY_ZERO",
            "MIN_QTY",
            "MIN_NOTIONAL",
            "SAFE_SIZE_BELOW_EXCHANGE_MIN",

            "TIER_CAPITAL_CEILING_EXCEEDED",

            "SAFE_SIZE_ZERO",
            "RISK_ENTRY_BLOCK",
            "RISK_EXCEPTION",
        ],

        "semantics":{
            "exchange_min_qty_or_notional":
                "USE_SYNTHETIC_OR_FRACTIONAL_QTY_FOR_RESEARCH_METRICS_ONLY",

            "tier_capital_ceiling":
                "RECORD_WOULD_BLOCK_AND_CALCULATE_HYPOTHETICAL_SIZE",

            "risk_entry_block":
                "RECORD_WOULD_BLOCK_AND_CONTINUE_COUNTERFACTUAL_LIFECYCLE",

            "late_entry":
                "STAGE1_COUNTERFACTUAL_POLICY_PRESERVED",
        },

        "forbidden":[
            "REAL_ORDER",
            "REAL_POSITION",
            "REAL_FILL",
            "LIVE_GATE_BYPASS",
            "CANARY_QUALIFICATION_SUBSTITUTE",
            "FORMAL_MINIMAL_SHADOW_PASS_SUBSTITUTE",
        ],
    }


def runner_test_stage2_counterfactual_observation(
    reason="",
    *,
    requested_qty=0.0,
    synthetic_qty=0.0,
    planned_notional=0.0,
    capital_ceiling=0.0,
):
    reason=str(reason or "").upper()

    policy=(
        runner_test_stage2_counterfactual_policy()
    )

    allowed=set(
        policy.get(
            "would_block_continue_reasons"
        )
        or []
    )

    active=reason in allowed

    return {
        "active":
            bool(active),

        "decision":
            (
                "WOULD_BLOCK_CONTINUE_COUNTERFACTUAL"
                if active
                else "NO_STAGE2_OVERRIDE"
            ),

        "would_block_reason":
            reason,

        "requested_qty":
            float(requested_qty or 0.0),

        "synthetic_qty":
            float(synthetic_qty or 0.0),

        "planned_notional":
            float(planned_notional or 0.0),

        "capital_ceiling":
            float(capital_ceiling or 0.0),

        "real_order_authority":
            False,

        "live_eligible":
            False,

        "canary_qualification_credit":
            False,
    }


# ============================================================
# RUNNER_TEST_STAGE2_EXECUTION_WIRING_V1
# Research/Test Shadow observation only.
# ZERO real-order / real-position authority.
# ============================================================

def _runner_stage2_float(value):
    try:
        out=float(value)
        if out == out:
            return out
    except Exception:
        pass
    return 0.0


def _runner_stage2_pick(context, gate, names):
    for source in (context,gate):
        if not isinstance(source,dict):
            continue
        for name in names:
            if name in source:
                value=source.get(name)
                if isinstance(value,(int,float)):
                    return _runner_stage2_float(value)
    return 0.0


def _runner_stage2_pick_bool(context, gate, names):
    for source in (context,gate):
        if not isinstance(source,dict):
            continue
        for name in names:
            if name in source:
                value=source.get(name)
                if isinstance(value,bool):
                    return value
    return None


def _runner_stage2_reasons(context, gate):
    out=[]
    names=(
        "reason",
        "block_reason",
        "entry_block_reason",
        "would_block_reason",
        "risk_reason",
        "qty_reason",
        "allocation_reason",
    )

    for source in (context,gate):
        if not isinstance(source,dict):
            continue

        for name in names:
            value=source.get(name)

            if isinstance(value,str):
                value=value.strip().upper()

                if value:
                    out.append(value)

    return list(dict.fromkeys(out))


def runner_test_stage2_execute_counterfactual(
    context,
    gate,
    *,
    producer="",
):
    if not isinstance(context,dict):
        context={}

    if not isinstance(gate,dict):
        gate={}

    reasons=_runner_stage2_reasons(
        context,
        gate,
    )

    requested_qty=_runner_stage2_pick(
        context,
        gate,
        (
            "requested_qty",
            "raw_qty",
            "candidate_qty",
            "qty",
        ),
    )

    actual_qty=_runner_stage2_pick(
        context,
        gate,
        (
            "adjusted_qty",
            "qty",
            "approved_qty",
            "safe_size_qty",
        ),
    )

    approved_qty=_runner_stage2_pick(
        context,
        gate,
        (
            "approved_qty",
            "safe_size_qty",
            "reduced_qty",
        ),
    )

    price=_runner_stage2_pick(
        context,
        gate,
        (
            "price",
            "entry_price",
            "mark_price",
        ),
    )

    min_qty=_runner_stage2_pick(
        context,
        gate,
        (
            "min_qty",
            "exchange_min_qty",
        ),
    )

    min_notional=_runner_stage2_pick(
        context,
        gate,
        (
            "min_notional",
            "exchange_min_notional",
        ),
    )

    planned_notional=_runner_stage2_pick(
        context,
        gate,
        (
            "planned_notional",
            "notional",
            "requested_notional",
        ),
    )

    capital_ceiling=_runner_stage2_pick(
        context,
        gate,
        (
            "capital_ceiling",
            "tier_capital_ceiling",
        ),
    )

    # ---------------------------------------------
    # Exchange-filter counterfactual
    # ---------------------------------------------

    if requested_qty>0 and actual_qty<=0:
        reasons.append(
            "CALC_QTY_ZERO"
        )

    if (
        min_qty>0
        and requested_qty>0
        and requested_qty<min_qty
    ):
        reasons.append(
            "MIN_QTY"
        )

    requested_notional=(
        requested_qty*price
        if requested_qty>0 and price>0
        else planned_notional
    )

    if (
        min_notional>0
        and requested_notional>0
        and requested_notional<min_notional
    ):
        reasons.append(
            "MIN_NOTIONAL"
        )

    # ---------------------------------------------
    # Tier-cap counterfactual
    # ---------------------------------------------

    if (
        planned_notional>0
        and capital_ceiling>0
        and planned_notional>capital_ceiling
    ):
        reasons.append(
            "TIER_CAPITAL_CEILING_EXCEEDED"
        )

    # ---------------------------------------------
    # Risk counterfactual
    # ---------------------------------------------

    risk_pass=_runner_stage2_pick_bool(
        context,
        gate,
        (
            "entry_risk_safe_size_pass",
            "risk_entry_pass",
            "risk_allow",
        ),
    )

    if risk_pass is False:
        reasons.append(
            "RISK_ENTRY_BLOCK"
        )

    if (
        requested_qty>0
        and approved_qty<=0
        and risk_pass is False
    ):
        reasons.append(
            "SAFE_SIZE_ZERO"
        )

    reasons=[
        str(x).upper()
        for x in reasons
        if str(x or "").strip()
    ]

    reasons=list(dict.fromkeys(reasons))

    allowed=set(
        runner_test_stage2_counterfactual_policy()
        .get(
            "would_block_continue_reasons"
        )
        or []
    )

    matched=[
        x for x in reasons
        if x in allowed
    ]

    synthetic_qty=max(
        requested_qty,
        actual_qty,
        approved_qty,
        min_qty,
        (
            min_notional/price
            if min_notional>0 and price>0
            else 0.0
        ),
    )

    hypothetical_notional=max(
        planned_notional,
        (
            synthetic_qty*price
            if synthetic_qty>0 and price>0
            else 0.0
        ),
    )

    active=bool(matched)

    return {
        "schema":
            "B_BOT_RUNNER_TEST_STAGE2_EXECUTION_V1",

        "producer":
            str(producer or ""),

        "active":
            active,

        "decision":
            (
                "WOULD_BLOCK_CONTINUE_COUNTERFACTUAL"
                if active
                else
                "NO_WOULD_BLOCK_DETECTED"
            ),

        "would_block_reasons":
            matched,

        "all_observed_reasons":
            reasons,

        "requested_qty":
            float(requested_qty),

        "actual_qty":
            float(actual_qty),

        "approved_qty":
            float(approved_qty),

        "synthetic_qty":
            float(synthetic_qty),

        "price":
            float(price),

        "min_qty":
            float(min_qty),

        "min_notional":
            float(min_notional),

        "planned_notional":
            float(planned_notional),

        "capital_ceiling":
            float(capital_ceiling),

        "hypothetical_notional":
            float(hypothetical_notional),

        "continue_counterfactual_lifecycle":
            active,

        "observation_only":
            True,

        "real_order_authority":
            False,

        "real_position_authority":
            False,

        "live_eligible":
            False,

        "formal_shadow_credit":
            False,

        "canary_qualification_credit":
            False,
    }


def shadow_pass_from_gate(
    gate,
):

    required = (
        gate.get(
            "runtime_errors"
        ) == 0

        and gate.get(
            "closed_bar_timing_pass"
        ) is True

        and gate.get(
            "signal_logic_parity_pass"
        ) is True

        and gate.get(
            "state_transition_pass"
        ) is True

        and gate.get(
            "order_simulation_cost_sanity_pass"
        ) is True

        and gate.get(
            "strategy_instance_policy_snapshot_pass"
        ) is True

        and (
            (
                gate.get("individual_trailing_snapshot_binding_pass") is True
                and gate.get("individual_reentry_snapshot_binding_pass") is True
            )
            or (
                gate.get("policy_contract_mode") == "LEGACY_FROZEN_BASELINE_V1"
                and gate.get("legacy_frozen_baseline_policy_pass") is True
                and gate.get("individual_trailing_snapshot_binding_pass") is False
                and gate.get("individual_reentry_snapshot_binding_pass") is False
            )
        )

        and gate.get(
            "order_quantity_calculation_pass"
        ) is True

        and gate.get(
            "duplicate_or_missing_action_integrity_pass"
        ) is True

        and gate.get(
            "portfolio_allocation_gate_integration_pass"
        ) is True
    )

    if not required:
        return False

    try:

        lifecycle_count = int(
            gate.get(
                "complete_lifecycles"
            )
            or 0
        )

    except Exception:

        lifecycle_count = 0

    replay_substitute = (
        gate.get(
            "replay_dryrun_state_transition_pass"
        ) is True

        and gate.get(
            "live_closed_bar_confirmation_pass"
        ) is True
    )

    return bool(
        lifecycle_count >= 1
        or replay_substitute
    )


# ================================================================================================
# LEGACY_MULTI_STRATEGY_BUNDLE_SHADOW_V1
#
# Legacy validated bundle support.
#
# This is NOT a synthetic composite strategy source.
# Runtime registry remains:
#   strategy   = EMA_CROSS+SAR
#   strategies = [EMA_CROSS, SAR]
#
# The adapter binds and executes the exact component source files,
# then applies the same Gate direction + all-components consensus contract.
# ================================================================================================

_LEGACY_BUNDLE_SHADOW_CONTRACTS = {
    (
        "SV_12H_STANDARD_EMA_CROSS_SAR_V1",
        "12H",
        "EMA_CROSS+SAR",
    ): {
        "schema":
            "B_BOT_V3_LEGACY_MULTI_STRATEGY_BUNDLE_SHADOW_V1",

        "strategy_version":
            "SV_12H_STANDARD_EMA_CROSS_SAR_V1",

        "timeframe":
            "12H",

        "strategy":
            "EMA_CROSS+SAR",

        "consensus":
            "ALL_COMPONENT_DIRECTIONS_AGREE",

        "signal_direction_contract":
            "GATE_SIGNAL_THEN_STRENGTH_V1",

        "components": [
            {
                "strategy":
                    "EMA_CROSS",

                "source_file":
                    "assist/strategies/ema_cross.py",

                "sha256":
                    "51602b22052c160679e111cfd1064e88d0cb353dbfb63ccbb42f17b1ff4116c2",
            },
            {
                "strategy":
                    "SAR",

                "source_file":
                    "assist/strategies/sar.py",

                "sha256":
                    "264b71be3c1abb9686c21a35c4fd76550039229d07e7529813e2cdefe145012c",
            },
        ],
    },
}


def _bundle_shadow_contract_for(
    version_id,
    timeframe,
    strategy,
):

    key = (
        str(version_id or ""),
        str(timeframe or "").upper(),
        str(strategy or "").upper(),
    )

    row = (
        _LEGACY_BUNDLE_SHADOW_CONTRACTS
        .get(key)
    )

    if not isinstance(
        row,
        dict,
    ):
        return None

    # Return an isolated copy.
    return json.loads(
        json.dumps(
            row,
            ensure_ascii=False,
        )
    )


def _load_signal_only_module(
    source,
    version_id,
    strategy,
):

    module_name = (
        "_bbot_bundle_shadow_"
        + hashlib.sha256(
            (
                str(version_id)
                + "|"
                + str(strategy)
                + "|"
                + str(source)
            ).encode(
                "utf-8"
            )
        ).hexdigest()[:16]
    )

    spec = (
        importlib.util
        .spec_from_file_location(
            module_name,
            source,
        )
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise RuntimeError(
            "BUNDLE_MODULE_SPEC_INVALID"
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

    if not callable(
        getattr(
            module,
            "signal",
            None,
        )
    ):
        raise RuntimeError(
            "BUNDLE_COMPONENT_SIGNAL_MISSING:"
            + str(strategy)
        )

    # Shadow-local imported component only.
    # Prevent SAR trace logging from producing thousands of replay lines.
    component_logger = getattr(
        module,
        "logger",
        None,
    )

    if component_logger is not None:

        try:
            component_logger.disabled = True

        except Exception:
            pass

    return module


def _bundle_shadow_direction(
    row,
):

    if not isinstance(
        row,
        dict,
    ):
        return "HOLD"

    signal = str(
        row.get("signal")
        or "wait"
    ).lower()

    if signal in {
        "long",
        "buy",
        "bull",
        "bullish",
    }:
        return "LONG"

    if signal in {
        "short",
        "sell",
        "bear",
        "bearish",
    }:
        return "SHORT"

    try:

        long_strength = float(
            row.get(
                "strength_long"
            )
            or 0.0
        )

    except Exception:

        long_strength = 0.0

    try:

        short_strength = float(
            row.get(
                "strength_short"
            )
            or 0.0
        )

    except Exception:

        short_strength = 0.0

    if (
        long_strength > 0
        and short_strength <= 0
    ):
        return "LONG"

    if (
        short_strength > 0
        and long_strength <= 0
    ):
        return "SHORT"

    return "HOLD"


class _LegacyBundleShadowModule:

    def __init__(
        self,
        components,
    ):

        self._components = list(
            components
        )

    def _decision(
        self,
        df,
    ):

        directions = []
        details = {}

        for (
            name,
            module,
        ) in self._components:

            row = module.signal(
                df.copy()
            )

            if not isinstance(
                row,
                dict,
            ):
                row = {}

            direction = (
                _bundle_shadow_direction(
                    row
                )
            )

            directions.append(
                direction
            )

            details[name] = {
                "direction":
                    direction,

                "signal":
                    row.get(
                        "signal",
                        "wait",
                    ),

                "state":
                    row.get(
                        "state",
                        "wait",
                    ),

                "strength_long":
                    row.get(
                        "strength_long",
                        0.0,
                    ),

                "strength_short":
                    row.get(
                        "strength_short",
                        0.0,
                    ),

                "reason":
                    row.get(
                        "reason",
                        "",
                    ),
            }

        if (
            directions
            and all(
                x == "LONG"
                for x in directions
            )
        ):

            decision = "LONG"

        elif (
            directions
            and all(
                x == "SHORT"
                for x in directions
            )
        ):

            decision = "SHORT"

        else:

            decision = "HOLD"

        return (
            decision,
            details,
        )

    def signal(
        self,
        df,
        params=None,
    ):

        (
            decision,
            details,
        ) = self._decision(
            df
        )

        if decision == "LONG":

            return {
                "signal":
                    "long",

                "state":
                    "long",

                "strength_long":
                    1.0,

                "strength_short":
                    0.0,

                "reason":
                    "BUNDLE_ALL_LONG",

                "bundle_details":
                    details,
            }

        if decision == "SHORT":

            return {
                "signal":
                    "short",

                "state":
                    "short",

                "strength_long":
                    0.0,

                "strength_short":
                    1.0,

                "reason":
                    "BUNDLE_ALL_SHORT",

                "bundle_details":
                    details,
            }

        return {
            "signal":
                "wait",

            "state":
                "wait",

            "strength_long":
                0.0,

            "strength_short":
                0.0,

            "reason":
                "BUNDLE_NO_CONSENSUS",

            "bundle_details":
                details,
        }

    def generate_signals(
        self,
        df,
        timeframe=None,
    ):

        n = len(df)

        out = np.zeros(
            n,
            dtype=np.int8,
        )

        # Exact closed-prefix replay.
        # Each output at index i sees only rows <= i.
        for i in range(n):

            (
                decision,
                _,
            ) = self._decision(
                df.iloc[
                    : i + 1
                ].copy()
            )

            if decision == "LONG":

                out[i] = 1

            elif decision == "SHORT":

                out[i] = -1

        return out


def _load_bundle_shadow_adapter(
    version_id,
    strategy,
    timeframe,
    contract,
):

    expected = (
        _bundle_shadow_contract_for(
            version_id,
            timeframe,
            strategy,
        )
    )

    if expected is None:
        raise RuntimeError(
            "BUNDLE_CONTRACT_UNSUPPORTED"
        )

    if not isinstance(
        contract,
        dict,
    ):
        raise RuntimeError(
            "BUNDLE_CONTRACT_INVALID"
        )

    requested_components = (
        contract.get(
            "components"
        )
        or []
    )

    if (
        requested_components
        != expected.get(
            "components"
        )
    ):
        raise RuntimeError(
            "BUNDLE_CONTRACT_COMPONENT_DRIFT"
        )

    registry = load_json(
        VERSION_REGISTRY,
        {},
    )

    version_row = (
        (
            registry.get(
                "strategy_versions"
            )
            or {}
        ).get(
            str(version_id)
        )
        or {}
    )

    if not isinstance(
        version_row,
        dict,
    ):
        raise RuntimeError(
            "BUNDLE_VERSION_ROW_MISSING"
        )

    if str(
        version_row.get("kind")
        or ""
    ).upper() != "STRATEGY":
        raise RuntimeError(
            "BUNDLE_VERSION_KIND_INVALID"
        )

    if str(
        version_row.get("status")
        or ""
    ).upper() not in {
        "VALIDATED",
        "PROVISIONAL",
        "CANARY",
        "SMALL",
        "NORMAL",
    }:
        raise RuntimeError(
            "BUNDLE_VERSION_STATUS_INVALID"
        )

    payload = (
        version_row.get(
            "payload"
        )
        if isinstance(
            version_row.get(
                "payload"
            ),
            dict,
        )
        else {}
    )

    if str(
        payload.get("strategy")
        or ""
    ).upper() != str(
        strategy
        or ""
    ).upper():
        raise RuntimeError(
            "BUNDLE_REGISTRY_STRATEGY_MISMATCH"
        )

    if str(
        payload.get("timeframe")
        or ""
    ).upper() != str(
        timeframe
        or ""
    ).upper():
        raise RuntimeError(
            "BUNDLE_REGISTRY_TIMEFRAME_MISMATCH"
        )

    registered_components = [
        str(x).upper()
        for x in (
            payload.get(
                "strategies"
            )
            or []
        )
    ]

    expected_names = [
        str(
            x.get(
                "strategy"
            )
            or ""
        ).upper()
        for x in expected[
            "components"
        ]
    ]

    if (
        registered_components
        != expected_names
    ):
        raise RuntimeError(
            "BUNDLE_REGISTRY_COMPONENT_MISMATCH"
        )

    # A bundle must stay a bundle.
    # Never accept a fabricated composite source_file.
    if str(
        payload.get("source_file")
        or ""
    ).strip():
        raise RuntimeError(
            "BUNDLE_FAKE_COMPOSITE_SOURCE_FORBIDDEN"
        )

    loaded = []
    validated_sources = []

    for item in expected[
        "components"
    ]:

        name = str(
            item.get(
                "strategy"
            )
            or ""
        ).upper()

        source_rel = _normalize_rel(
            item.get(
                "source_file"
            )
        )

        source = _safe_root_file(
            source_rel
        )

        if source is None:
            raise RuntimeError(
                "BUNDLE_COMPONENT_SOURCE_MISSING:"
                + name
            )

        actual_sha = sha256(
            source
        )

        expected_sha = str(
            item.get(
                "sha256"
            )
            or ""
        ).lower()

        if (
            actual_sha.lower()
            != expected_sha
        ):
            raise RuntimeError(
                "BUNDLE_COMPONENT_HASH_MISMATCH:"
                + name
            )

        module = (
            _load_signal_only_module(
                source,
                version_id,
                name,
            )
        )

        loaded.append(
            (
                name,
                module,
            )
        )

        validated_sources.append(
            {
                "strategy":
                    name,

                "source_file":
                    source_rel,

                "sha256":
                    actual_sha,
            }
        )

    return (
        _LegacyBundleShadowModule(
            loaded
        ),
        validated_sources,
    )

def produce_shadow_evidence(
    timeframe,
    frame_state,
):

    tf = str(
        timeframe
        or ""
    ).upper()

    fr = (
        frame_state
        if isinstance(
            frame_state,
            dict,
        )
        else {}
    )

    version_id = str(
        fr.get(
            "strategy_version"
        )
        or ""
    )

    strategy_instance_id = str(
        fr.get(
            "strategy_instance_id"
        )
        or ""
    ).strip()

    strategy = str(
        fr.get(
            "strategy"
        )
        or ""
    ).upper()

    request = (
        fr.get(
            "pending_request_template"
        )
        if isinstance(
            fr.get(
                "pending_request_template"
            ),
            dict,
        )
        else {}
    )

    errors = []
    waits = []

    source_rel = (
        _normalize_rel(
            request.get(
                "strategy_file"
            )
        )
    )

    source = (
        _safe_root_file(
            source_rel
        )
    )

    bundle_contract = (
        request.get(
            "bundle_shadow_contract"
        )
        if isinstance(
            request.get(
                "bundle_shadow_contract"
            ),
            dict,
        )
        else {}
    )

    bundle_shadow = bool(
        bundle_contract
    )

    validated_sources = []

    binding_pass = False
    module = None

    if (
        not version_id
        or not strategy_instance_id
        or not strategy
        or not tf
    ):
        errors.append(
            "EXACT_STRATEGY_INSTANCE_IDENTITY_MISSING"
        )

    else:
        binding_pass = (
            _version_binding(
                version_id,
                strategy,
                tf,
                source_rel,
                strategy_instance_id,
            )
        )

        if not binding_pass:
            errors.append(
                "STRATEGY_INSTANCE_VERSION_SOURCE_BINDING_FAIL"
            )

        elif bundle_shadow:
            try:
                (
                    module,
                    validated_sources,
                ) = (
                    _load_bundle_shadow_adapter(
                        version_id,
                        strategy,
                        tf,
                        bundle_contract,
                    )
                )

            except Exception as error:
                errors.append(
                    "BUNDLE_BINDING:"
                    + type(error).__name__
                    + ":"
                    + str(error)[:300]
                )

        elif source is None:
            errors.append(
                "VALIDATED_SOURCE_MISSING"
            )

        else:
            try:
                module = (
                    _load_module(
                        source,
                        version_id,
                    )
                )

            except Exception as error:
                errors.append(
                    "MODULE_LOAD:"
                    + type(error).__name__
                    + ":"
                    + str(error)[:300]
                )

    market_frames = []
    cache_reason = ""

    if module is not None:

        (
            market_frames,
            cache_reason,
        ) = _market_frames(timeframe)

        if cache_reason:

            waits.append(
                cache_reason
            )

    prefix_results = []
    parity_results = []
    freshness_results = []
    real_signal_sets = []

    if (
        module is not None
        and market_frames
    ):

        for (
            symbol,
            frame,
        ) in market_frames:

            try:

                sig = (
                    _signal_array(
                        module,
                        frame,
                        tf,
                    )
                )

                real_signal_sets.append(
                    (
                        symbol,
                        sig,
                    )
                )

                prefix_tf = (
                    _prefix_invariant(
                        module,
                        frame,
                        tf,
                    )
                )

                prefix_live = (
                    _prefix_invariant(
                        module,
                        frame,
                        "LIVE",
                    )
                )

                parity = (
                    _wrapper_parity(
                        module,
                        frame,
                    )
                )

                (
                    fresh,
                    latest,
                    age,
                ) = _closed_bar_fresh(
                    frame,
                    tf,
                )

                prefix_results.append(
                    bool(
                        prefix_tf
                        and prefix_live
                    )
                )

                parity_results.append(
                    bool(
                        parity
                    )
                )

                freshness_results.append(
                    {
                        "symbol":
                            symbol,

                        "pass":
                            bool(
                                fresh
                            ),

                        "latest_ts":
                            latest,

                        "age_seconds":
                            age,
                    }
                )

            except Exception as error:

                errors.append(
                    "REAL_CACHE_EXECUTION:"
                    + symbol
                    + ":"
                    + type(
                        error
                    ).__name__
                    + ":"
                    + str(
                        error
                    )[:300]
                )

    closed_bar_timing_pass = bool(
        prefix_results
        and all(
            prefix_results
        )
    )

    signal_logic_parity_pass = bool(
        parity_results
        and all(
            parity_results
        )
    )

    live_closed_bar_confirmation_pass = bool(
        freshness_results

        and all(
            bool(
                row.get(
                    "pass"
                )
            )
            for row
            in freshness_results
        )

        and closed_bar_timing_pass

        and signal_logic_parity_pass
    )

    transition_pass = False
    transition_source = ""
    transition_events = []

    for (
        symbol,
        signals,
    ) in real_signal_sets:

        (
            ok,
            events,
        ) = _dryrun_transition(
            signals
        )

        if ok:

            transition_pass = True

            transition_source = (
                "LOCAL_CLOSED_BAR_CACHE:"
                + symbol
            )

            transition_events = (
                events
            )

            break

    if (
        not transition_pass
        and module is not None
    ):

        for (
            name,
            frame,
        ) in _synthetic_frames():

            try:

                signals = (
                    _signal_array(
                        module,
                        frame,
                        tf,
                    )
                )

                (
                    ok,
                    events,
                ) = _dryrun_transition(
                    signals
                )

                if ok:

                    transition_pass = True

                    transition_source = (
                        "DETERMINISTIC_REPLAY:"
                        + name
                    )

                    transition_events = (
                        events
                    )

                    break

            except Exception as error:

                waits.append(
                    "REPLAY_NOT_USABLE:"
                    + name
                    + ":"
                    + type(
                        error
                    ).__name__
                )

    (
        cost_pass,
        cost_details,
    ) = _cost_sanity(
        request,
        tf,
    )

    if not cost_pass:

        waits.append(
            "CANARY_COST_SIMULATION_PENDING"
        )

    snapshot_pass,snapshot_details=_strategy_instance_policy_snapshot_check(fr,request)
    if not snapshot_pass:
        errors.extend(snapshot_details.get("errors") or ["STRATEGY_INSTANCE_POLICY_SNAPSHOT_FAIL"])

    action_pass,action_details=_action_sequence_integrity(transition_events) if transition_pass else (False,{"reason":"STATE_TRANSITION_NOT_AVAILABLE"})
    if not action_pass:
        errors.append("DUPLICATE_OR_MISSING_ACTION_INTEGRITY_FAIL")

    portfolio_pass,portfolio_details=_portfolio_gate_integration_check(version_id)
    if not portfolio_pass:
        errors.append("PORTFOLIO_ALLOCATION_GATE_INTEGRATION_FAIL")

    order_quantity_pass=bool(
        cost_pass
        and math.isfinite(float((cost_details or {}).get("simulated_exposure") or 0.0))
        and float((cost_details or {}).get("simulated_exposure") or 0.0)>0.0
        and abs(float((cost_details or {}).get("capital_weight") or 0.0)-0.20)<=1e-12
    )
    if not order_quantity_pass:
        errors.append("ORDER_QUANTITY_CALCULATION_FAIL")

    gate = {
        "runtime_errors":
            len(errors),

        "closed_bar_timing_pass":
            bool(
                closed_bar_timing_pass
            ),

        "signal_logic_parity_pass":
            bool(
                signal_logic_parity_pass
            ),

        "state_transition_pass":
            bool(
                transition_pass
            ),

        "order_simulation_cost_sanity_pass":
            bool(
                cost_pass
            ),

        "strategy_instance_policy_snapshot_pass":
            bool(snapshot_pass),

        "individual_trailing_snapshot_binding_pass":
            bool((snapshot_details or {}).get("trailing_binding_pass")),

        "individual_reentry_snapshot_binding_pass":
            bool((snapshot_details or {}).get("reentry_binding_pass")),

        # Distinct evidence: baseline parity must not impersonate optimized bindings.
        "policy_contract_mode":
            str((snapshot_details or {}).get("policy_contract_mode") or ""),
        "legacy_frozen_baseline_policy_pass":
            (snapshot_details or {}).get("legacy_frozen_baseline_policy_pass") is True,

        "order_quantity_calculation_pass":
            bool(order_quantity_pass),

        "duplicate_or_missing_action_integrity_pass":
            bool(action_pass),

        "portfolio_allocation_gate_integration_pass":
            bool(portfolio_pass),

        # No real pre-Canary trade is fabricated.
        "complete_lifecycles":
            0,

        "lifecycle_count":
            0,

        "replay_dryrun_state_transition_pass":
            bool(
                transition_pass
            ),

        "live_closed_bar_confirmation_pass":
            bool(
                live_closed_bar_confirmation_pass
            ),
        "runner_test_stage2_counterfactual_policy": runner_test_stage2_counterfactual_policy(),
        "runner_test_stage2_observation_only": True,
        "runner_test_stage2_canary_credit": False,
    }

    # RUNNER_TEST_STAGE2_EXECUTION_WIRING_V1
    _runner_stage2_obs = runner_test_stage2_execute_counterfactual(locals(), gate, producer='produce_shadow_evidence')
    gate["runner_test_stage2_counterfactual_observation"] = _runner_stage2_obs
    gate["runner_test_stage2_execution_wired"] = True
    gate["runner_test_stage2_formal_shadow_credit"] = False
    gate["runner_test_stage2_canary_credit"] = False

    gate_pass = (
        shadow_pass_from_gate(
            gate
        )
    )

    evidence = {
        "schema":
            SCHEMA,

        "updated_at":
            now(),

        "pass":
            bool(
                gate_pass
            ),

        "version_id":
            version_id,

        "strategy_version":
            version_id,

        "strategy_instance_id":
            strategy_instance_id,

        "strategy_policy_bundle_version":
            str(
                request.get(
                    "strategy_policy_bundle_version"
                )
                or fr.get(
                    "strategy_policy_bundle_version"
                )
                or ""
            ),

        "strategy":
            strategy,

        "timeframe":
            tf,

        "role":
            ROLE,

        "real_order":
            False,

        "server_mutation":
            False,

        "evidence_mode":
            "LOCAL_RESEARCH_SHADOW_REPLAY",

        "validated_source":
            source_rel,

        "validated_source_sha256":
            (
                sha256(
                    source
                )
                if source
                is not None
                else ""
            ),

        "version_source_binding_pass":
            bool(
                binding_pass
            ),

        "minimal_runtime_gate":
            gate,

        "closed_bar_samples":
            freshness_results,

        "transition_replay": {
            "source":
                transition_source,

            "events":
                transition_events,
        },

        "cost_sanity":
            cost_details,

        "strategy_instance_policy_snapshot":
            snapshot_details,

        "action_integrity":
            action_details,

        "portfolio_allocation_gate_integration":
            portfolio_details,

        "waiting_reasons":
            waits,

        "errors":
            errors,
    }

    if bundle_shadow:

        evidence[
            "evidence_mode"
        ] = (
            "LOCAL_RESEARCH_BUNDLE_SHADOW_REPLAY"
        )

        evidence[
            "bundle_shadow"
        ] = True

        evidence[
            "bundle_components"
        ] = [
            str(
                x.get(
                    "strategy"
                )
                or ""
            ).upper()
            for x in validated_sources
        ]

        evidence[
            "bundle_consensus"
        ] = (
            "ALL_COMPONENT_DIRECTIONS_AGREE"
        )

        evidence[
            "bundle_signal_direction_contract"
        ] = (
            "GATE_SIGNAL_THEN_STRENGTH_V1"
        )

        evidence[
            "validated_sources"
        ] = validated_sources

        evidence[
            "bundle_source_set_sha256"
        ] = hashlib.sha256(
            json.dumps(
                validated_sources,
                ensure_ascii=False,
                sort_keys=True,
                separators=(
                    ",",
                    ":",
                ),
            ).encode(
                "utf-8"
            )
        ).hexdigest()

    path = (
        _evidence_path(
            version_id,
            strategy_instance_id,
        )
    )

    atomic_json(
        path,
        evidence,
    )

    return evidence




def _policy_apply_patch(
    target,
    patch,
):

    if not isinstance(
        target,
        dict,
    ):
        raise RuntimeError(
            "POLICY_TARGET_NOT_OBJECT"
        )

    if not isinstance(
        patch,
        dict,
    ):
        raise RuntimeError(
            "POLICY_PATCH_NOT_OBJECT"
        )

    for key, value in (
        patch.items()
    ):

        if (
            isinstance(
                value,
                dict,
            )
            and isinstance(
                target.get(
                    key
                ),
                dict,
            )
        ):

            _policy_apply_patch(
                target[
                    key
                ],
                value,
            )

        else:

            target[
                key
            ] = json.loads(
                json.dumps(
                    value
                )
            )


def _policy_all_numbers_finite(
    value,
):

    if isinstance(
        value,
        bool,
    ):
        return True

    if isinstance(
        value,
        (
            int,
            float,
        ),
    ):

        try:

            return math.isfinite(
                float(
                    value
                )
            )

        except Exception:

            return False

    if isinstance(
        value,
        dict,
    ):

        return all(
            _policy_all_numbers_finite(
                item
            )
            for item
            in value.values()
        )

    if isinstance(
        value,
        (
            list,
            tuple,
        ),
    ):

        return all(
            _policy_all_numbers_finite(
                item
            )
            for item
            in value
        )

    return True


def _policy_runtime_config_valid(
    cfg_path,
):

    env = os.environ.copy()

    env[
        "BBOT_LOCAL_ROOT"
    ] = str(
        ROOT
    )

    env[
        "BOT_BASE_DIR"
    ] = str(
        ROOT
    )

    env[
        "STRATEGY_CONFIG_FILE"
    ] = str(
        cfg_path
    )

    env[
        "RUNNER_DRY_RUN"
    ] = "1"

    current_pythonpath = str(
        env.get(
            "PYTHONPATH"
        )
        or ""
    )

    env[
        "PYTHONPATH"
    ] = (
        str(
            ROOT
        )
        + (
            os.pathsep
            + current_pythonpath
            if current_pythonpath
            else ""
        )
    )

    code = (
        "import strategy_runtime,sys;"
        "strategy_runtime.reload();"
        "v=strategy_runtime.validate();"
        "print(v);"
        "sys.exit(0 if "
        "isinstance(v,dict) and "
        "v.get('ok') else 2)"
    )

    try:

        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                code,
            ],
            cwd=str(
                ROOT
            ),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    except Exception as error:

        return (
            False,
            type(
                error
            ).__name__
            + ":"
            + str(
                error
            )[:300],
        )

    detail = (
        (
            proc.stdout
            or proc.stderr
            or ""
        )[-2000:]
    )

    return (
        proc.returncode == 0,
        detail,
    )


def _policy_live_closed_bar_check():

    frames, reason = (
        _market_frames("1H")
    )

    if not frames:

        return (
            False,
            [],
            reason
            or "NO_MARKET_FRAME",
        )

    details = []

    for symbol, frame in frames:

        (
            fresh,
            latest,
            age,
        ) = _closed_bar_fresh(
            frame,
            "1H",
        )

        details.append(
            {
                "symbol":
                    symbol,

                "pass":
                    bool(
                        fresh
                    ),

                "latest_ts":
                    latest,

                "age_seconds":
                    age,
            }
        )

    ok = bool(
        details
        and all(
            row.get(
                "pass"
            )
            is True
            for row
            in details
        )
    )

    return (
        ok,
        details,
        "",
    )


def _policy_snapshot_transition_pass(
    pending_cfg,
    track,
    version_id,
):

    policy_versions = (
        pending_cfg.get(
            "policy_versions"
        )
        or {}
    )

    if str(
        policy_versions.get(
            track
        )
        or ""
    ) != str(
        version_id
    ):
        return (
            False,
            [],
        )

    snapshot = {
        "policy_versions":
            json.loads(
                json.dumps(
                    policy_versions
                )
            ),

        "trailing":
            json.loads(
                json.dumps(
                    pending_cfg.get(
                        "trailing"
                    )
                    or {}
                )
            ),

        "opposite_exit":
            json.loads(
                json.dumps(
                    pending_cfg.get(
                        "opposite_exit"
                    )
                    or {}
                )
            ),

        "state_reentry":
            json.loads(
                json.dumps(
                    pending_cfg.get(
                        "state_reentry"
                    )
                    or {}
                )
            ),

        "opposite_reopen":
            json.loads(
                json.dumps(
                    pending_cfg.get(
                        "opposite_reopen"
                    )
                    or {}
                )
            ),

        "v3_decision":
            json.loads(
                json.dumps(
                    pending_cfg.get(
                        "v3_decision"
                    )
                    or {}
                )
            ),
    }

    baseline = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
    )

    events = []

    for state_name in (
        "ENTRY",
        "HOLD",
        "EXIT",
        "REENTRY",
    ):

        owned = json.loads(
            json.dumps(
                snapshot
            )
        )

        preserved = (
            json.dumps(
                owned,
                ensure_ascii=False,
                sort_keys=True,
            )
            == baseline
        )

        version_preserved = (
            str(
                (
                    owned.get(
                        "policy_versions"
                    )
                    or {}
                ).get(
                    track
                )
                or ""
            )
            == str(
                version_id
            )
        )

        events.append(
            {
                "state":
                    state_name,

                "snapshot_preserved":
                    bool(
                        preserved
                    ),

                "policy_version_preserved":
                    bool(
                        version_preserved
                    ),
            }
        )

    return (
        all(
            row[
                "snapshot_preserved"
            ]
            and row[
                "policy_version_preserved"
            ]
            for row
            in events
        ),
        events,
    )


def produce_policy_shadow_evidence(
    policy_track,
    policy_state,
    strategy_instance_target=None,
):

    track = str(
        policy_track
        or ""
    ).upper()

    state_row = (
        policy_state
        if isinstance(
            policy_state,
            dict,
        )
        else {}
    )

    target = (
        strategy_instance_target
        if isinstance(
            strategy_instance_target,
            dict,
        )
        else {}
    )

    strategy_instance_id = str(
        target.get(
            "strategy_instance_id"
        )
        or ""
    ).strip()

    exact_strategy_version = str(
        target.get(
            "strategy_version"
        )
        or ""
    ).strip()

    target_strategy = str(
        target.get(
            "strategy"
        )
        or ""
    ).upper()

    target_timeframe = str(
        target.get(
            "timeframe"
        )
        or ""
    ).upper()

    version_id = str(
        state_row.get(
            "policy_version"
        )
        or ""
    )

    request = (
        state_row.get(
            "pending_request_template"
        )
        if isinstance(
            state_row.get(
                "pending_request_template"
            ),
            dict,
        )
        else {}
    )

    errors = []
    waits = []

    registry = load_json(
        VERSION_REGISTRY,
        {},
    )

    version_row = (
        (
            registry.get(
                "policy_versions"
            )
            or {}
        ).get(
            version_id
        )
        or {}
    )

    if not isinstance(
        version_row,
        dict,
    ) or not version_row:

        errors.append(
            "POLICY_VERSION_NOT_FOUND"
        )

    registry_track = str(
        version_row.get(
            "track"
        )
        or ""
    ).upper()

    if (
        version_row
        and registry_track
        != track
    ):

        errors.append(
            "POLICY_TRACK_BINDING_FAIL"
        )

    if str(
        version_row.get(
            "status"
        )
        or ""
    ).upper() not in {
        "VALIDATED",
        "CANARY",
        "SMALL",
        "NORMAL",
    }:

        errors.append(
            "POLICY_VERSION_STATUS_INVALID"
        )

    if target:
        instance = (
            (
                registry.get(
                    "strategy_instances"
                )
                or {}
            ).get(
                strategy_instance_id
            )
            or {}
        )

        if (
            not strategy_instance_id
            or not exact_strategy_version
            or not target_strategy
            or not target_timeframe
        ):
            errors.append(
                "POLICY_EXACT_STRATEGY_INSTANCE_IDENTITY_MISSING"
            )

        elif not isinstance(instance, dict) or not instance:
            errors.append(
                "POLICY_STRATEGY_INSTANCE_NOT_FOUND"
            )

        else:
            if str(
                instance.get(
                    "exact_strategy_version"
                )
                or ""
            ) != exact_strategy_version:
                errors.append(
                    "POLICY_STRATEGY_INSTANCE_VERSION_BINDING_FAIL"
                )

            if str(
                instance.get(
                    "strategy_id"
                )
                or ""
            ).upper() != target_strategy:
                errors.append(
                    "POLICY_STRATEGY_INSTANCE_STRATEGY_BINDING_FAIL"
                )

            if str(
                instance.get(
                    "timeframe"
                )
                or ""
            ).upper() != target_timeframe:
                errors.append(
                    "POLICY_STRATEGY_INSTANCE_TIMEFRAME_BINDING_FAIL"
                )

            if str(
                instance.get(
                    "strategy_policy_bundle_version"
                )
                or ""
            ) != str(
                state_row.get(
                    "strategy_policy_bundle_version"
                )
                or ""
            ):
                errors.append(
                    "POLICY_STRATEGY_INSTANCE_BUNDLE_BINDING_FAIL"
                )

            if str(
                instance.get(
                    "status"
                )
                or ""
            ).upper() != "LOCAL_SHADOW":
                errors.append(
                    "POLICY_STRATEGY_INSTANCE_NOT_LOCAL_SHADOW"
                )

    payload = (
        version_row.get(
            "payload"
        )
        if isinstance(
            version_row.get(
                "payload"
            ),
            dict,
        )
        else {}
    )

    patch = (
        payload.get(
            "config_patch"
        )
        if isinstance(
            payload.get(
                "config_patch"
            ),
            dict,
        )
        else {}
    )

    allowed = {
        "ENTRY_RISK": {
            "v3_decision",
        },

        "EXIT": {
            "trailing",
            "opposite_exit",
        },

        "REENTRY": {
            "state_reentry",
            "opposite_reopen",
        },

        "SYMBOL": {
            "v3_decision",
        },

        "PORTFOLIO": {
            "v3_decision",
        },

        "REGIME": {
            "v3_decision",
        },

        "EXECUTION": {
            "v3_decision",
        },

        "DIRECTION": {
            "v3_decision",
        },
    }

    patch_scope_pass = bool(
        patch
        and set(
            patch
        ).issubset(
            allowed.get(
                track,
                set(),
            )
        )
    )

    if not patch_scope_pass:

        errors.append(
            "POLICY_PATCH_SCOPE_INVALID"
        )

    request_binding_pass = (
        str(
            request.get(
                "version_kind"
            )
            or ""
        ).upper()
        in {
            "POLICY",
            "STRATEGY_INSTANCE_POLICY_BUNDLE",
        }

        and str(
            request.get(
                "policy_track"
            )
            or ""
        ).upper()
        == track

        and str(
            request.get(
                "policy_version"
            )
            or ""
        )
        == version_id

        and str(
            request.get(
                "tier"
            )
            or ""
        ).upper()
        == "CANARY"

        and request.get(
            "autonomous_rule_passed"
        )
        is True

        and request.get(
            "core_mutation"
        )
        is False

    )

    required_gate = str(
        request.get(
            "required_pre_runtime_gate"
        )
        or ""
    )

    if target:
        if required_gate != (
            "EXACT_STRATEGY_INSTANCE_MINIMAL_SHADOW_RUNTIME_SAFETY"
        ):
            request_binding_pass = False
    elif required_gate != "MINIMAL_SHADOW_RUNTIME_SAFETY":
        request_binding_pass = False

    if target:
        request_targets = (
            request.get(
                "strategy_instances"
            )
            if isinstance(
                request.get(
                    "strategy_instances"
                ),
                list,
            )
            else []
        )

        target_request_bound = any(
            isinstance(row, dict)
            and str(
                row.get(
                    "strategy_instance_id"
                )
                or ""
            ) == strategy_instance_id
            and str(
                row.get(
                    "strategy_version"
                )
                or ""
            ) == exact_strategy_version
            for row in request_targets
        )

        if str(
            request.get(
                "version_kind"
            )
            or ""
        ).upper() != "STRATEGY_INSTANCE_POLICY_BUNDLE":
            request_binding_pass = False

        if not target_request_bound:
            request_binding_pass = False

    if not request_binding_pass:

        errors.append(
            "POLICY_REQUEST_BINDING_FAIL"
        )

    signal_artifact_unchanged = (
        str(
            request.get(
                "strategy_file"
            )
            or ""
        )
        == ""

        and str(
            request.get(
                "strategy"
            )
            or ""
        )
        == ""

        and str(
            request.get(
                "timeframe"
            )
            or ""
        )
        == ""
    )

    if not signal_artifact_unchanged:

        errors.append(
            "POLICY_REQUEST_MUTATES_STRATEGY_ARTIFACT"
        )

    cfg_rel = (
        _normalize_rel(
            request.get(
                "strategy_config_file"
            )
        )
    )

    cfg_path = (
        ROOT
        / cfg_rel
    ).resolve()

    cfg_path_safe = False

    try:

        cfg_path.relative_to(
            ROOT.resolve()
        )

        cfg_path_safe = (
            cfg_path.is_file()
            and cfg_path.name.startswith(
                "strategy_config_pending_policy_"
            )
            and cfg_path.suffix.lower()
            == ".json"
        )

    except Exception:

        cfg_path_safe = False

    if not cfg_path_safe:

        errors.append(
            "POLICY_PENDING_CONFIG_PATH_INVALID"
        )

    pending_cfg = (
        load_json(
            cfg_path,
            {},
        )
        if cfg_path_safe
        else {}
    )

    base_cfg = load_json(
        ROOT
        / "strategy_config.json",
        {},
    )

    pending_version_pass = (
        isinstance(
            pending_cfg,
            dict,
        )
        and str(
            (
                pending_cfg.get(
                    "policy_versions"
                )
                or {}
            ).get(
                track
            )
            or ""
        )
        == version_id
    )

    if not pending_version_pass:

        errors.append(
            "POLICY_PENDING_VERSION_BINDING_FAIL"
        )

    expected_cfg = {}

    expected_match = False
    frames_unchanged = False

    if (
        isinstance(
            base_cfg,
            dict,
        )
        and isinstance(
            pending_cfg,
            dict,
        )
        and patch_scope_pass
    ):

        expected_cfg = json.loads(
            json.dumps(
                base_cfg
            )
        )

        try:

            _policy_apply_patch(
                expected_cfg,
                patch,
            )

            expected_cfg.setdefault(
                "policy_versions",
                {},
            )[track] = version_id

            expected_match = (
                pending_cfg
                == expected_cfg
            )

        except Exception as error:

            errors.append(
                "POLICY_EXPECTED_CONFIG_BUILD:"
                + type(
                    error
                ).__name__
                + ":"
                + str(
                    error
                )[:300]
            )

        frames_unchanged = (
            pending_cfg.get(
                "frames"
            )
            ==
            base_cfg.get(
                "frames"
            )
        )

    if not expected_match:

        errors.append(
            "POLICY_PENDING_CONFIG_NOT_EXACT_PATCH"
        )

    if not frames_unchanged:

        errors.append(
            "POLICY_PATCH_CHANGED_STRATEGY_FRAMES"
        )

    runtime_valid = False
    runtime_detail = ""

    if cfg_path_safe:

        (
            runtime_valid,
            runtime_detail,
        ) = _policy_runtime_config_valid(
            cfg_path
        )

    if not runtime_valid:

        errors.append(
            "POLICY_RUNTIME_CONFIG_VALIDATION_FAIL"
        )

    finite_pass = (
        _policy_all_numbers_finite(
            pending_cfg
        )
    )

    if not finite_pass:

        errors.append(
            "POLICY_NONFINITE_NUMERIC_VALUE"
        )

    additional_entry_safe = (
        (
            pending_cfg.get(
                "v3_decision"
            )
            or {}
        ).get(
            "additional_entry_same_symbol"
        )
        is not True
    )

    if not additional_entry_safe:

        errors.append(
            "POLICY_ADDITIONAL_ENTRY_FORBIDDEN"
        )

    trailing_dynamic_safe = (
        (
            pending_cfg.get(
                "trailing"
            )
            or {}
        ).get(
            "dynamic_adjustment"
        )
        is not True
    )

    if not trailing_dynamic_safe:

        errors.append(
            "POLICY_DYNAMIC_TRAILING_FORBIDDEN"
        )

    (
        transition_pass,
        transition_events,
    ) = _policy_snapshot_transition_pass(
        pending_cfg,
        track,
        version_id,
    )

    if not transition_pass:

        errors.append(
            "POLICY_SNAPSHOT_TRANSITION_FAIL"
        )

    (
        live_closed_bar_pass,
        live_closed_bar_details,
        live_reason,
    ) = _policy_live_closed_bar_check()

    if not live_closed_bar_pass:

        waits.append(
            live_reason
            or
            "LIVE_CLOSED_BAR_CONFIRMATION_PENDING"
        )

    config_only_pass = (
        patch_scope_pass
        and request_binding_pass
        and signal_artifact_unchanged
        and pending_version_pass
        and expected_match
        and frames_unchanged
    )

    cost_sanity_pass = (
        runtime_valid
        and finite_pass
        and frames_unchanged
        and additional_entry_safe
        and trailing_dynamic_safe
        and request.get(
            "core_mutation"
        )
        is False
    )

    gate = {
        "runtime_errors":
            len(
                errors
            ),

        "closed_bar_timing_pass":
            bool(
                config_only_pass
                and live_closed_bar_pass
            ),

        "signal_logic_parity_pass":
            bool(
                config_only_pass
                and frames_unchanged
            ),

        "state_transition_pass":
            bool(
                transition_pass
            ),

        "order_simulation_cost_sanity_pass":
            bool(
                cost_sanity_pass
            ),

        "complete_lifecycles":
            0,

        "lifecycle_count":
            0,

        "replay_dryrun_state_transition_pass":
            bool(
                transition_pass
            ),

        "live_closed_bar_confirmation_pass":
            bool(
                live_closed_bar_pass
            ),
        "runner_test_stage2_counterfactual_policy": runner_test_stage2_counterfactual_policy(),
        "runner_test_stage2_observation_only": True,
        "runner_test_stage2_canary_credit": False,
    }

    # RUNNER_TEST_STAGE2_EXECUTION_WIRING_V1
    _runner_stage2_obs = runner_test_stage2_execute_counterfactual(locals(), gate, producer='produce_policy_shadow_evidence')
    gate["runner_test_stage2_counterfactual_observation"] = _runner_stage2_obs
    gate["runner_test_stage2_execution_wired"] = True
    gate["runner_test_stage2_formal_shadow_credit"] = False
    gate["runner_test_stage2_canary_credit"] = False

    gate_pass = (
        shadow_pass_from_gate(
            gate
        )
    )

    evidence = {
        "schema":
            SCHEMA,

        "updated_at":
            now(),

        "pass":
            bool(
                gate_pass
            ),

        "version_id":
            (
                exact_strategy_version
                if target
                else version_id
            ),

        "version_kind":
            (
                "STRATEGY_INSTANCE_POLICY_BUNDLE"
                if target
                else "POLICY"
            ),

        "strategy_instance_id":
            strategy_instance_id,

        "strategy":
            target_strategy,

        "timeframe":
            target_timeframe,

        "strategy_policy_bundle_version":
            str(
                state_row.get(
                    "strategy_policy_bundle_version"
                )
                or ""
            ),

        "policy_version":
            version_id,

        "policy_track":
            track,

        "role":
            ROLE,

        "real_order":
            False,

        "server_mutation":
            False,

        "evidence_mode":
            "LOCAL_RESEARCH_POLICY_SHADOW_REPLAY",

        "pending_config":
            cfg_rel,

        "policy_patch":
            patch,

        "policy_patch_scope_pass":
            bool(
                patch_scope_pass
            ),

        "request_binding_pass":
            bool(
                request_binding_pass
            ),

        "signal_artifact_unchanged":
            bool(
                signal_artifact_unchanged
            ),

        "pending_version_binding_pass":
            bool(
                pending_version_pass
            ),

        "exact_patch_reconstruction_pass":
            bool(
                expected_match
            ),

        "strategy_frames_unchanged":
            bool(
                frames_unchanged
            ),

        "runtime_config_validation_pass":
            bool(
                runtime_valid
            ),

        "runtime_config_validation_detail":
            runtime_detail,

        "minimal_runtime_gate":
            gate,

        "closed_bar_samples":
            live_closed_bar_details,

        "policy_state_transition_replay":
            transition_events,

        "waiting_reasons":
            waits,

        "errors":
            errors,
    }

    atomic_json(
        _evidence_path(
            (
                exact_strategy_version
                if target
                else version_id
            ),
            strategy_instance_id,
        ),
        evidence,
    )

    return evidence



def _legacy_scale_shadow_fail(
    tf,
    strategy,
    version_id,
    tier,
    reason,
):

    gate = {
        "runtime_errors":
            1,

        "closed_bar_timing_pass":
            False,

        "signal_logic_parity_pass":
            False,

        "state_transition_pass":
            False,

        "order_simulation_cost_sanity_pass":
            False,

        "complete_lifecycles":
            0,

        "lifecycle_count":
            0,

        "replay_dryrun_state_transition_pass":
            False,

        "live_closed_bar_confirmation_pass":
            False,
        "runner_test_stage2_counterfactual_policy": runner_test_stage2_counterfactual_policy(),
        "runner_test_stage2_observation_only": True,
        "runner_test_stage2_canary_credit": False,
    }

    evidence = {
        "schema":
            SCHEMA,

        "updated_at":
            now(),

        "pass":
            False,

        "version_id":
            version_id,

        "version_kind":
            "STRATEGY",

        "strategy":
            strategy,

        "timeframe":
            tf,

        "role":
            ROLE,

        "real_order":
            False,

        "server_mutation":
            False,

        "evidence_mode":
            "LEGACY_CANARY_SHADOW_FAIL_CLOSED",

        "legacy_scale_shadow":
            True,

        "legacy_runtime_tier":
            tier,

        "minimal_runtime_gate":
            gate,

        "waiting_reasons":
            [],

        "errors":
            [
                str(
                    reason
                )
            ],
    }

    atomic_json(
        _evidence_path(
            version_id
        ),
        evidence,
    )

    return evidence


def produce_legacy_scale_shadow_evidence(
    timeframe,
    frame_state,
):
    """
    LEGACY_CANARY_SHADOW / LEGACY_SCALE_SHADOW

    Existing CANARY/SMALL runtime tier is NEVER changed here.
    This function produces only local Shadow evidence for the
    exact already-running Strategy Version.
    """

    tf = str(
        timeframe
        or ""
    ).upper()

    original = (
        frame_state
        if isinstance(
            frame_state,
            dict,
        )
        else {}
    )

    tier = str(
        original.get(
            "tier"
        )
        or ""
    ).upper()

    strategy = str(
        original.get(
            "strategy"
        )
        or ""
    ).upper()

    version_id = str(
        original.get(
            "strategy_version"
        )
        or ""
    )

    if tier not in {
        "CANARY",
        "SMALL",
    }:

        return _legacy_scale_shadow_fail(
            tf,
            strategy,
            version_id,
            tier,
            "LEGACY_SCALE_TIER_INVALID",
        )

    if not (
        tf
        and strategy
        and version_id
    ):

        return _legacy_scale_shadow_fail(
            tf,
            strategy,
            version_id,
            tier,
            "LEGACY_SCALE_BINDING_MISSING",
        )

    registry = load_json(
        VERSION_REGISTRY,
        {},
    )

    version_row = (
        (
            registry.get(
                "strategy_versions"
            )
            or {}
        ).get(
            version_id
        )
        or {}
    )

    if not isinstance(
        version_row,
        dict,
    ) or not version_row:

        return _legacy_scale_shadow_fail(
            tf,
            strategy,
            version_id,
            tier,
            "LEGACY_SCALE_VERSION_NOT_FOUND",
        )

    if str(
        version_row.get(
            "kind"
        )
        or ""
    ).upper() != "STRATEGY":

        return _legacy_scale_shadow_fail(
            tf,
            strategy,
            version_id,
            tier,
            "LEGACY_SCALE_KIND_MISMATCH",
        )

    if str(
        version_row.get(
            "track"
        )
        or ""
    ).upper() != "STRATEGY":

        return _legacy_scale_shadow_fail(
            tf,
            strategy,
            version_id,
            tier,
            "LEGACY_SCALE_TRACK_MISMATCH",
        )

    registry_status = str(
        version_row.get(
            "status"
        )
        or ""
    ).upper()

    if registry_status not in {
        "VALIDATED",
        "CANARY",
        "SMALL",
        "NORMAL",
    }:

        return _legacy_scale_shadow_fail(
            tf,
            strategy,
            version_id,
            tier,
            "LEGACY_SCALE_VERSION_STATUS_INVALID:"
            + registry_status,
        )

    payload = (
        version_row.get(
            "payload"
        )
        if isinstance(
            version_row.get(
                "payload"
            ),
            dict,
        )
        else {}
    )

    if str(
        payload.get(
            "strategy"
        )
        or ""
    ).upper() != strategy:

        return _legacy_scale_shadow_fail(
            tf,
            strategy,
            version_id,
            tier,
            "LEGACY_SCALE_STRATEGY_BINDING_FAIL",
        )

    if str(
        payload.get(
            "timeframe"
        )
        or ""
    ).upper() != tf:

        return _legacy_scale_shadow_fail(
            tf,
            strategy,
            version_id,
            tier,
            "LEGACY_SCALE_TIMEFRAME_BINDING_FAIL",
        )

    payload_strategies = [
        str(x).upper()
        for x in (
            payload.get(
                "strategies"
            )
            or []
        )
    ]

    bundle_contract = (
        _bundle_shadow_contract_for(
            version_id,
            tf,
            strategy,
        )
    )

    source_rel = ""
    source_path = None

    # GENERATED_COMBINATION_SINGLE_SOURCE_SHADOW_V1
    #
    # generated_combinations/*.py is already one self-contained strategy
    # source.  Its registry payload legitimately contains
    # strategies=[strategy_itself], but that must not be interpreted as a
    # multi-file runtime bundle such as the legacy 12H EMA_CROSS+SAR pair.
    generated_source_rel = _normalize_rel(
        payload.get("source_file")
    )

    generated_source_key = (
        str(generated_source_rel or "")
        .replace("\\", "/")
        .lower()
    )

    self_contained_generated_strategy = bool(
        len(payload_strategies) == 1
        and payload_strategies[0]
        == str(strategy or "").upper()
        and generated_source_key.startswith(
            "strategy_lab/results/v3/generated_combinations/"
        )
        and generated_source_key.endswith(".py")
    )

    if (
        payload_strategies
        and not self_contained_generated_strategy
    ):

        if bundle_contract is None:

            return _legacy_scale_shadow_fail(
                tf,
                strategy,
                version_id,
                tier,
                "LEGACY_SCALE_BUNDLE_UNSUPPORTED",
            )

        expected_components = [
            str(
                x.get(
                    "strategy"
                )
                or ""
            ).upper()
            for x in (
                bundle_contract.get(
                    "components"
                )
                or []
            )
        ]

        if (
            payload_strategies
            != expected_components
        ):

            return _legacy_scale_shadow_fail(
                tf,
                strategy,
                version_id,
                tier,
                "LEGACY_SCALE_BUNDLE_COMPONENT_BINDING_FAIL",
            )

    else:

        source_rel = _normalize_rel(
            payload.get(
                "source_file"
            )
        )

        source_path = (
            ROOT
            / source_rel
        ).resolve()

        try:

            source_path.relative_to(
                ROOT.resolve()
            )

        except Exception:

            return _legacy_scale_shadow_fail(
                tf,
                strategy,
                version_id,
                tier,
                "LEGACY_SCALE_SOURCE_OUTSIDE_ROOT",
            )

        if not source_path.is_file():

            return _legacy_scale_shadow_fail(
                tf,
                strategy,
                version_id,
                tier,
                "LEGACY_SCALE_SOURCE_MISSING",
            )

    cfg = load_json(
        ROOT
        / "strategy_config.json",
        {},
    )

    if not isinstance(
        cfg,
        dict,
    ):

        return _legacy_scale_shadow_fail(
            tf,
            strategy,
            version_id,
            tier,
            "LEGACY_SCALE_CONFIG_INVALID",
        )

    cfg_frame = (
        (
            cfg.get(
                "frames"
            )
            or {}
        ).get(
            tf
        )
        or {}
    )

    cfg_version = str(
        cfg_frame.get(
            "strategy_version"
        )
        or ""
    )

    configured = [
        str(x).upper()
        for x
        in (
            cfg_frame.get(
                "strategies"
            )
            or []
        )
    ]

    direct_strategy = str(
        cfg_frame.get(
            "strategy"
        )
        or ""
    ).upper()

    strategy_bound = (
        strategy
        in configured

        or direct_strategy
        == strategy

        or (
            configured
            and "+".join(
                configured
            )
            == strategy
        )
    )

    if (
        cfg_version
        != version_id
        or not strategy_bound
    ):

        return _legacy_scale_shadow_fail(
            tf,
            strategy,
            version_id,
            tier,
            "LEGACY_SCALE_ACTIVE_CONFIG_BINDING_FAIL",
        )

    if bundle_contract is not None:

        snapshot_path = (
            ROOT
            / "strategy_config.json"
        )

    else:

        snapshot_path = (
            RES
            / "legacy_shadow_inputs"
            / version_id
            / (
                tf.lower()
                + "_strategy_config.json"
            )
        )

        snapshot_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        atomic_json(
            snapshot_path,
            cfg,
        )

    legacy_state = json.loads(
        json.dumps(
            original
        )
    )

    # This is ONLY a copied evidence input.
    # The real deployment_state tier is not modified.
    legacy_state[
        "tier"
    ] = "PENDING_SHADOW"

    legacy_state[
        "shadow_status"
    ] = "PENDING"

    legacy_state.setdefault(
        "source_row",
        version_row.get(
            "evidence"
        )
        or {},
    )

    legacy_state[
        "shadow_evidence_file"
    ] = str(
        _evidence_path(
            version_id
        ).relative_to(
            ROOT
        )
    )

    legacy_state[
        "pending_request_template"
    ] = {
        "schema":
            "B_BOT_V3_RUNTIME_SYNC_REQUEST_V1",

        "request_id":
            (
                "LEGACY_SHADOW_"
                + tf
                + "_"
                + version_id
            ),

        "created_at":
            now(),

        "reason":
            "LEGACY_CANARY_SHADOW_RUNTIME_SAFETY",

        "version_kind":
            "STRATEGY",

        "timeframe":
            tf,

        "strategy":
            strategy,

        "strategy_version":
            version_id,

        "policy_versions":
            cfg.get(
                "policy_versions"
            )
            or {},

        "tier":
            "CANARY",

        "canary_capital_weight":
            0.20,

        "strategy_file":
            source_rel,

        "strategy_config_file":
            str(
                snapshot_path.relative_to(
                    ROOT
                )
            ),

        "previous_frame_config":
            original.get(
                "previous_frame_config"
            )
            or {},

        "research_evidence":
            version_row.get(
                "evidence"
            )
            or {},

        "required_pre_runtime_gate":
            "MINIMAL_SHADOW_RUNTIME_SAFETY",

        "autonomous_rule_passed":
            True,

        "core_mutation":
            False,

        "legacy_scale_shadow":
            True,

        "legacy_runtime_tier":
            tier,
    }

    if bundle_contract is not None:

        pending = (
            legacy_state.get(
                "pending_request_template"
            )
            or {}
        )

        pending[
            "strategies"
        ] = [
            str(
                x.get(
                    "strategy"
                )
                or ""
            ).upper()
            for x in (
                bundle_contract.get(
                    "components"
                )
                or []
            )
        ]

        pending[
            "strategy_files"
        ] = [
            str(
                x.get(
                    "source_file"
                )
                or ""
            )
            for x in (
                bundle_contract.get(
                    "components"
                )
                or []
            )
        ]

        pending[
            "bundle_shadow_contract"
        ] = bundle_contract

        legacy_state[
            "pending_request_template"
        ] = pending

    try:

        evidence = produce_shadow_evidence(tf, legacy_state)

    except Exception as error:

        return _legacy_scale_shadow_fail(
            tf,
            strategy,
            version_id,
            tier,
            (
                "LEGACY_SCALE_PRODUCER_EXCEPTION:"
                + type(
                    error
                ).__name__
                + ":"
                + str(
                    error
                )[:500]
            ),
        )

    if not isinstance(
        evidence,
        dict,
    ):

        return _legacy_scale_shadow_fail(
            tf,
            strategy,
            version_id,
            tier,
            "LEGACY_SCALE_PRODUCER_NOT_OBJECT",
        )

    if str(
        evidence.get(
            "version_id"
        )
        or ""
    ) != version_id:

        return _legacy_scale_shadow_fail(
            tf,
            strategy,
            version_id,
            tier,
            "LEGACY_SCALE_EVIDENCE_VERSION_MISMATCH",
        )

    existing_kind = str(
        evidence.get(
            "version_kind"
        )
        or ""
    ).upper()

    if existing_kind not in {
        "",
        "STRATEGY",
    }:

        return _legacy_scale_shadow_fail(
            tf,
            strategy,
            version_id,
            tier,
            "LEGACY_SCALE_EVIDENCE_KIND_MISMATCH",
        )

    # Never invent PASS. Existing producer decides pass/fail.
    evidence[
        "version_kind"
    ] = "STRATEGY"

    evidence[
        "legacy_scale_shadow"
    ] = True

    evidence[
        "legacy_runtime_tier"
    ] = tier

    atomic_json(
        _evidence_path(
            version_id
        ),
        evidence,
    )

    return evidence

def process_pending_shadows():

    deployment = load_json(
        STATE,
        {},
    )

    frames = (
        deployment.get(
            "frames"
        )
        or {}
    )

    results = []

    pending_strategy_rows = []
    seen_strategy_instances = set()

    for timeframe, fr in frames.items():
        if not isinstance(fr, dict):
            continue

        sid = str(
            fr.get("strategy_instance_id")
            or ""
        ).strip()

        pending_strategy_rows.append(
            (
                str(timeframe).upper(),
                fr,
            )
        )

        if sid:
            seen_strategy_instances.add(sid)

    for sid, row in (
        deployment.get(
            "strategy_instances"
        )
        or {}
    ).items():

        if not isinstance(row, dict):
            continue

        sid = str(
            row.get("strategy_instance_id")
            or sid
            or ""
        ).strip()

        if sid and sid in seen_strategy_instances:
            continue

        if str(
            row.get("tier")
            or ""
        ).upper() != "PENDING_SHADOW":
            continue

        timeframe = str(
            row.get("timeframe")
            or ""
        ).upper()

        if not timeframe:
            continue

        pending_strategy_rows.append(
            (
                timeframe,
                row,
            )
        )

        if sid:
            seen_strategy_instances.add(sid)

    for (
        timeframe,
        fr,
    ) in pending_strategy_rows:

        if not isinstance(
            fr,
            dict,
        ):
            continue

        if str(
            fr.get(
                "tier"
            )
            or ""
        ).upper() != (
            "PENDING_SHADOW"
        ):

            continue

        version_id = str(
            fr.get(
                "strategy_version"
            )
            or ""
        )

        strategy_instance_id = str(
            fr.get(
                "strategy_instance_id"
            )
            or ""
        ).strip()

        if not strategy_instance_id:
            results.append(
                {
                    "schema": SCHEMA,
                    "updated_at": now(),
                    "pass": False,
                    "version_id": version_id,
                    "strategy_instance_id": "",
                    "strategy": str(
                        fr.get("strategy") or ""
                    ).upper(),
                    "timeframe": str(timeframe).upper(),
                    "role": ROLE,
                    "real_order": False,
                    "server_mutation": False,
                    "errors": [
                        "EXACT_STRATEGY_INSTANCE_IDENTITY_MISSING"
                    ],
                }
            )
            continue

        existing = (
            load_json(
                _evidence_path(
                    version_id,
                    strategy_instance_id,
                ),
                {},
            )
        )

        if (
            isinstance(
                existing,
                dict,
            )

            and existing.get(
                "pass"
            ) is True

            and str(
                existing.get(
                    "version_id"
                )
                or ""
            ) == version_id

            and str(
                existing.get(
                    "strategy_instance_id"
                )
                or ""
            ) == strategy_instance_id

            and str(
                existing.get(
                    "strategy"
                )
                or ""
            ).upper()
            == str(
                fr.get(
                    "strategy"
                )
                or ""
            ).upper()

            and str(
                existing.get(
                    "timeframe"
                )
                or ""
            ).upper()
            == str(
                timeframe
            ).upper()
        ):

            results.append(
                existing
            )

            continue

        result = (
            produce_shadow_evidence(
                timeframe,
                fr,
            )
        )

        results.append(
            result
        )


    # --------------------------------------------------------

    # --------------------------------------------------------
    # LEGACY_CANARY_SHADOW / LEGACY_SCALE_SHADOW
    #
    # Existing CANARY and SMALL tiers remain unchanged.
    # Only local Shadow evidence is generated.
    # --------------------------------------------------------

    legacy_frames = (
        deployment.get(
            "frames"
        )
        or {}
    )

    for legacy_tf, legacy_frame in (
        legacy_frames.items()
    ):

        if not isinstance(
            legacy_frame,
            dict,
        ):
            continue

        legacy_tier = str(
            legacy_frame.get(
                "tier"
            )
            or ""
        ).upper()

        if legacy_tier not in {
            "CANARY",
            "SMALL",
        }:
            continue

        legacy_version = str(
            legacy_frame.get(
                "strategy_version"
            )
            or ""
        )

        if not legacy_version:

            results.append(
                _legacy_scale_shadow_fail(
                    str(
                        legacy_tf
                    ).upper(),
                    str(
                        legacy_frame.get(
                            "strategy"
                        )
                        or ""
                    ).upper(),
                    "",
                    legacy_tier,
                    "LEGACY_SCALE_STRATEGY_VERSION_MISSING",
                )
            )

            continue

        existing = load_json(
            _evidence_path(
                legacy_version
            ),
            {},
        )

        existing_kind = str(
            existing.get(
                "version_kind"
            )
            or ""
        ).upper()

        if (
            isinstance(
                existing,
                dict,
            )

            and existing.get(
                "pass"
            )
            is True

            and str(
                existing.get(
                    "version_id"
                )
                or ""
            )
            == legacy_version

            and existing_kind
            in {
                "",
                "STRATEGY",
            }
        ):

            results.append(
                existing
            )

            continue

        results.append(
            produce_legacy_scale_shadow_evidence(
                legacy_tf,
                legacy_frame,
            )
        )

    # Policy Version / Strategy Instance Policy Bundle Shadow
    # --------------------------------------------------------

    policies = (
        deployment.get(
            "policies"
        )
        or {}
    )

    for policy_track, policy_state in (
        policies.items()
    ):

        if not isinstance(
            policy_state,
            dict,
        ):
            continue

        if str(
            policy_state.get(
                "tier"
            )
            or ""
        ).upper() != "PENDING_SHADOW":
            continue

        policy_version = str(
            policy_state.get(
                "policy_version"
            )
            or ""
        )

        if not policy_version:

            results.append(
                {
                    "schema":
                        SCHEMA,

                    "updated_at":
                        now(),

                    "pass":
                        False,

                    "version_id":
                        "",

                    "version_kind":
                        "POLICY",

                    "policy_version":
                        "",

                    "policy_track":
                        str(
                            policy_track
                        ).upper(),

                    "role":
                        ROLE,

                    "real_order":
                        False,

                    "server_mutation":
                        False,

                    "errors":
                        [
                            "POLICY_VERSION_MISSING"
                        ],
                }
            )

            continue

        instance_targets = (
            policy_state.get(
                "strategy_instances"
            )
            if isinstance(
                policy_state.get(
                    "strategy_instances"
                ),
                list,
            )
            else []
        )

        if instance_targets:

            for target in instance_targets:

                if not isinstance(
                    target,
                    dict,
                ):
                    results.append(
                        {
                            "schema": SCHEMA,
                            "updated_at": now(),
                            "pass": False,
                            "version_id": "",
                            "version_kind":
                                "STRATEGY_INSTANCE_POLICY_BUNDLE",
                            "policy_version": policy_version,
                            "policy_track":
                                str(
                                    policy_track
                                ).upper(),
                            "role": ROLE,
                            "real_order": False,
                            "server_mutation": False,
                            "errors": [
                                "POLICY_STRATEGY_INSTANCE_TARGET_INVALID"
                            ],
                        }
                    )
                    continue

                strategy_instance_id = str(
                    target.get(
                        "strategy_instance_id"
                    )
                    or ""
                ).strip()

                exact_strategy_version = str(
                    target.get(
                        "strategy_version"
                    )
                    or ""
                ).strip()

                if (
                    not strategy_instance_id
                    or not exact_strategy_version
                ):
                    results.append(
                        {
                            "schema": SCHEMA,
                            "updated_at": now(),
                            "pass": False,
                            "version_id": exact_strategy_version,
                            "version_kind":
                                "STRATEGY_INSTANCE_POLICY_BUNDLE",
                            "strategy_instance_id":
                                strategy_instance_id,
                            "policy_version": policy_version,
                            "policy_track":
                                str(
                                    policy_track
                                ).upper(),
                            "role": ROLE,
                            "real_order": False,
                            "server_mutation": False,
                            "errors": [
                                "POLICY_EXACT_STRATEGY_INSTANCE_IDENTITY_MISSING"
                            ],
                        }
                    )
                    continue

                existing = load_json(
                    _evidence_path(
                        exact_strategy_version,
                        strategy_instance_id,
                    ),
                    {},
                )

                if (
                    isinstance(
                        existing,
                        dict,
                    )

                    and existing.get(
                        "pass"
                    ) is True

                    and str(
                        existing.get(
                            "version_id"
                        )
                        or ""
                    )
                    == exact_strategy_version

                    and str(
                        existing.get(
                            "version_kind"
                        )
                        or ""
                    ).upper()
                    == "STRATEGY_INSTANCE_POLICY_BUNDLE"

                    and str(
                        existing.get(
                            "strategy_instance_id"
                        )
                        or ""
                    )
                    == strategy_instance_id

                    and str(
                        existing.get(
                            "policy_version"
                        )
                        or ""
                    )
                    == policy_version

                    and str(
                        existing.get(
                            "policy_track"
                        )
                        or ""
                    ).upper()
                    == str(
                        policy_track
                    ).upper()
                ):

                    results.append(
                        existing
                    )

                    continue

                results.append(
                    produce_policy_shadow_evidence(
                        policy_track,
                        policy_state,
                        target,
                    )
                )

            continue

        # Legacy V3 policy state migration branch. New R4 policy qualification
        # always carries exact Strategy Instance targets and never reaches here.
        existing = load_json(
            _evidence_path(
                policy_version
            ),
            {},
        )

        if (
            isinstance(
                existing,
                dict,
            )

            and existing.get(
                "pass"
            ) is True

            and str(
                existing.get(
                    "version_id"
                )
                or ""
            )
            == policy_version

            and str(
                existing.get(
                    "version_kind"
                )
                or ""
            ).upper()
            == "POLICY"

            and str(
                existing.get(
                    "policy_track"
                )
                or ""
            ).upper()
            == str(
                policy_track
            ).upper()
        ):

            results.append(
                existing
            )

            continue

        results.append(
            produce_policy_shadow_evidence(
                policy_track,
                policy_state,
            )
        )

    return {
        "schema":
            "B_BOT_V3_SHADOW_PROCESS_RESULT_V1",

        "updated_at":
            now(),

        "processed":
            len(
                results
            ),

        "passed":
            sum(
                1
                for row
                in results
                if row.get(
                    "pass"
                ) is True
            ),

        "pending":
            sum(
                1
                for row
                in results
                if row.get(
                    "pass"
                ) is not True
            ),

        "results":
            results,
    }





def self_test():

    signals = np.asarray(
        [
            0,
            0,
            1,
            1,
            0,
            0,
            -1,
            -1,
            0,
        ],
        dtype=np.int8,
    )

    (
        transition,
        events,
    ) = _dryrun_transition(
        signals
    )

    gate = {
        "runtime_errors":
            0,

        "closed_bar_timing_pass":
            True,

        "signal_logic_parity_pass":
            True,

        "state_transition_pass":
            bool(
                transition
            ),

        "order_simulation_cost_sanity_pass":
            True,

        "strategy_instance_policy_snapshot_pass":
            True,

        "individual_trailing_snapshot_binding_pass":
            True,

        "individual_reentry_snapshot_binding_pass":
            True,

        "order_quantity_calculation_pass":
            True,

        "duplicate_or_missing_action_integrity_pass":
            True,

        "portfolio_allocation_gate_integration_pass":
            True,

        "complete_lifecycles":
            0,

        "replay_dryrun_state_transition_pass":
            bool(
                transition
            ),

        "live_closed_bar_confirmation_pass":
            True,
    }

    good = (
        shadow_pass_from_gate(
            gate
        )
    )

    bad = dict(
        gate
    )

    bad[
        "live_closed_bar_confirmation_pass"
    ] = False

    fail_closed = not (
        shadow_pass_from_gate(
            bad
        )
    )

    ok = bool(
        transition
        and events
        and good
        and fail_closed
    )

    return {
        "schema":
            "B_BOT_V3_SHADOW_ENGINE_SELF_TEST_V1",

        "ok":
            ok,

        "transition":
            transition,

        "fail_closed":
            fail_closed,

        "real_order":
            False,

        "server_mutation":
            False,
    }


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    parser.add_argument(
        "--once",
        action="store_true",
    )

    args = parser.parse_args()

    if args.self_test:

        out = self_test()

        print(
            json.dumps(
                out,
                ensure_ascii=False,
                indent=2,
            )
        )

        return (
            0
            if out.get(
                "ok"
            )
            else 2
        )

    if args.once:

        out = (
            process_pending_shadows()
        )

        print(
            json.dumps(
                out,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )

        return 0

    parser.error(
        "use --self-test or --once"
    )

    return 2


if __name__ == "__main__":

    raise SystemExit(
        main()
    )
