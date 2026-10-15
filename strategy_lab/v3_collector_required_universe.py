from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


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

CONFIG_JSON = (
    ROOT
    / "config.json"
)

STRATEGY_CONFIG = (
    ROOT
    / "strategy_config.json"
)

DEPLOYMENT_STATE = (
    RESULTS
    / "deployment_state.json"
)

VALIDATION = (
    RESULTS
    / "validation"
)

OUTPUT = (
    RESULTS
    / "collector_required_universe.json"
)

SYMBOL_RE = re.compile(
    r"^[A-Z0-9]{2,30}USDT$"
)


def load(path, default):

    try:

        return json.loads(
            Path(path).read_text(
                encoding="utf-8-sig"
            )
        )

    except Exception:

        return default


def symbol(value):

    value = str(
        value or ""
    ).strip().upper()

    if SYMBOL_RE.fullmatch(value):
        return value

    return ""


def extract(value):

    result = set()

    if isinstance(value, dict):

        for key, item in value.items():

            if str(key).lower() in {
                "symbol",
                "symbols",
                "core_symbols",
                "required_symbols",
            }:

                values = (
                    item
                    if isinstance(item, list)
                    else [item]
                )

                for raw in values:

                    found = symbol(raw)

                    if found:
                        result.add(found)

            result |= extract(item)

    elif isinstance(value, list):

        for item in value:
            result |= extract(item)

    return result


def atomic(obj):

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp = OUTPUT.with_name(
        OUTPUT.name
        + "."
        + str(os.getpid())
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
    )

    os.replace(
        tmp,
        OUTPUT,
    )


def main():

    config = load(
        CONFIG_JSON,
        {},
    )

    active = load(
        STRATEGY_CONFIG,
        {},
    )

    deployment = load(
        DEPLOYMENT_STATE,
        {},
    )

    # CONFIG_ENABLED_SYMBOLS

    config_enabled_symbols = set()

    values = (
        config.get("SYMBOLS")
        or {}
    )

    if isinstance(values, dict):

        for raw_symbol, settings in values.items():

            enabled = True

            if settings is False:
                enabled = False

            if (
                isinstance(settings, dict)
                and settings.get("enabled")
                is False
            ):
                enabled = False

            found = symbol(
                raw_symbol
            )

            if found and enabled:
                config_enabled_symbols.add(
                    found
                )

    # ACTIVE_STRATEGY_SYMBOLS

    active_strategy_symbols = (
        extract(active)
        | extract(deployment)
    )

    core_symbols = (
        active.get("core_symbols")
        or []
    )

    for raw in core_symbols:

        found = symbol(raw)

        if found:
            active_strategy_symbols.add(
                found
            )

    # SHADOW_REQUIRED_SYMBOLS
    # validation/<version>/shadow.json

    shadow_required_symbols = set()

    if VALIDATION.is_dir():

        for path in VALIDATION.glob(
            "*/shadow.json"
        ):

            obj = load(
                path,
                {},
            )

            shadow_required_symbols |= (
                extract(obj)
            )

    # Existing symbol-agnostic strategies still require
    # CORE live confirmation.

    if VALIDATION.is_dir():

        for raw in core_symbols:

            found = symbol(raw)

            if found:
                shadow_required_symbols.add(
                    found
                )

    # COLLECTOR_REQUIRED_UNIVERSE

    required_universe = sorted(
        set(config_enabled_symbols)
        | set(active_strategy_symbols)
        | set(shadow_required_symbols)
    )

    result = {
        "schema":
            "B_BOT_V3_COLLECTOR_REQUIRED_UNIVERSE_V1",

        "generated_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "config_enabled_symbols":
            sorted(
                config_enabled_symbols
            ),

        "active_strategy_symbols":
            sorted(
                active_strategy_symbols
            ),

        "shadow_required_symbols":
            sorted(
                shadow_required_symbols
            ),

        "required_universe":
            required_universe,

        "required_symbols":
            required_universe,
    }

    atomic(result)

    print(
        "COLLECTOR_REQUIRED_UNIVERSE=PASS"
    )

    print(
        "REQUIRED_COUNT="
        + str(
            len(required_universe)
        )
    )

    print(
        "REQUIRED_SYMBOLS="
        + ",".join(
            required_universe
        )
    )


if __name__ == "__main__":
    main()