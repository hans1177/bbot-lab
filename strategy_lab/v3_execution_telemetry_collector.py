# filename: v3_execution_telemetry_collector.py

"""
GREEN7A_REAL_EXECUTION_TELEMETRY_V1

Read-only execution telemetry collector.

Contracts:
- READ_ONLY_EXCHANGE_API
- EXACT_ORDER_ID_FILTER
- EXACT_STRATEGY_VERSION_FILTER
- CANARY_ONLY_SCOPE
- NO_HISTORICAL_VERSION_BACKFILL
- NO_SECRET_OUTPUT

This module does not create orders and does not import runner.

Exchange access is limited to authenticated user-trade reads when
fetch_account_trades_read_only() is explicitly called by a caller.
"""

from __future__ import annotations

import json

from v3_execution_cost_evidence import (
    execution_cost_evidence,
)
from v3_preorder_reference_observer import lookup_preorder_reference


MARKER = "GREEN7A_REAL_EXECUTION_TELEMETRY_V1"

READ_ONLY_EXCHANGE_API = True
EXACT_ORDER_ID_FILTER = True
EXACT_STRATEGY_VERSION_FILTER = True
CANARY_ONLY_SCOPE = True
NO_HISTORICAL_VERSION_BACKFILL = True
NO_SECRET_OUTPUT = True


def _text(value):
    return str(
        value
        or ""
    ).strip()


def _upper(value):
    return _text(
        value
    ).upper()


def _payload(row):
    if not isinstance(
        row,
        dict,
    ):
        return {}

    raw = row.get(
        "payload_json"
    )

    if isinstance(
        raw,
        dict,
    ):
        return raw

    if isinstance(
        raw,
        str,
    ):

        try:
            obj = json.loads(
                raw
            )

            if isinstance(
                obj,
                dict,
            ):
                return obj

        except Exception:
            return {}

    return {}


def _nested_dict(obj, key):
    value = (
        obj.get(key)
        if isinstance(
            obj,
            dict,
        )
        else None
    )

    return (
        value
        if isinstance(
            value,
            dict,
        )
        else {}
    )


def extract_strategy_version(
    history_row,
):
    """
    Exact-version only.

    Historical rows with no strategy_version are never backfilled.
    """

    if not isinstance(
        history_row,
        dict,
    ):
        return ""

    direct = _text(
        history_row.get(
            "strategy_version"
        )
    )

    if direct:
        return direct

    payload = _payload(
        history_row
    )

    for key in (
        "pending",
        "position",
        "gate",
        "candidate",
        "owner",
    ):

        row = _nested_dict(
            payload,
            key,
        )

        version = _text(
            row.get(
                "strategy_version"
            )
        )

        if version:
            return version

    return ""


def extract_order_id(
    history_row,
):
    if not isinstance(
        history_row,
        dict,
    ):
        return ""

    for key in (
        "entry_order_id",
        "close_order_id",
        "order_id",
        "orderId",
    ):

        value = _text(
            history_row.get(key)
        )

        if value:
            return value

    payload = _payload(
        history_row
    )

    for key in (
        "order",
        "close_response",
    ):

        row = _nested_dict(
            payload,
            key,
        )

        value = _text(
            row.get(
                "orderId"
            )
        )

        if value:
            return value

    return ""


def extract_entry_id(
    history_row,
):
    """
    Exact lifecycle entry_id only.

    No symbol/time/order heuristic.
    No historical backfill.
    """

    if not isinstance(
        history_row,
        dict,
    ):
        return ""

    direct = _text(
        history_row.get(
            "entry_id"
        )
    )

    if direct:
        return direct

    payload = _payload(
        history_row
    )

    for key in (
        "pending",
        "position",
        "candidate",
        "owner",
    ):

        row = _nested_dict(
            payload,
            key,
        )

        entry_id = _text(
            row.get(
                "entry_id"
            )
        )

        if entry_id:
            return entry_id

    return ""


def resolve_preorder_reference(
    history_row,
    eligibility,
    *,
    reference_price=None,
    preorder_reference_path=None,
):
    """
    Resolve exact durable pre-order execution reference.

    Normal live evidence path:
      entry_id
      -> preorder_reference
      -> exact strategy_version
      -> reference_price

    No current-price or post-fill backfill.
    """

    entry_id = extract_entry_id(
        history_row
    )

    expected_version = _text(
        (
            eligibility
            if isinstance(
                eligibility,
                dict,
            )
            else {}
        ).get(
            "strategy_version"
        )
    )

    # Existing explicit reference path is preserved only for
    # existing callers/tests that already supply a real reference.
    if reference_price is not None:

        try:
            explicit = float(
                reference_price
            )
        except Exception:

            return {
                "ok": False,
                "reason":
                    "EXPLICIT_REFERENCE_PRICE_INVALID",
                "entry_id":
                    entry_id,
                "reference_price":
                    None,
                "preorder_reference":
                    None,
            }

        if explicit <= 0.0:

            return {
                "ok": False,
                "reason":
                    "EXPLICIT_REFERENCE_PRICE_INVALID",
                "entry_id":
                    entry_id,
                "reference_price":
                    None,
                "preorder_reference":
                    None,
            }

        return {
            "ok": True,
            "reason":
                "EXPLICIT_REFERENCE_COMPATIBILITY",
            "entry_id":
                entry_id,
            "reference_price":
                explicit,
            "reference_source":
                "EXPLICIT_REFERENCE_COMPATIBILITY",
            "preorder_reference":
                None,
        }

    if not entry_id:

        return {
            "ok": False,
            "reason":
                "PREORDER_REFERENCE_ENTRY_ID_MISSING",
            "entry_id": "",
            "reference_price":
                None,
            "preorder_reference":
                None,
        }

    if preorder_reference_path is None:

        preorder_reference = (
            lookup_preorder_reference(
                entry_id
            )
        )

    else:

        preorder_reference = (
            lookup_preorder_reference(
                entry_id,
                evidence_path=
                    preorder_reference_path,
            )
        )

    if not isinstance(
        preorder_reference,
        dict,
    ):

        return {
            "ok": False,
            "reason":
                "PREORDER_REFERENCE_LOOKUP_INVALID",
            "entry_id":
                entry_id,
            "reference_price":
                None,
            "preorder_reference":
                None,
        }

    if not preorder_reference.get(
        "ok"
    ):

        return {
            "ok": False,
            "reason":
                _text(
                    preorder_reference.get(
                        "reason"
                    )
                )
                or
                "PREORDER_REFERENCE_NOT_FOUND",
            "entry_id":
                entry_id,
            "reference_price":
                None,
            "preorder_reference":
                preorder_reference,
        }

    durable_version = _text(
        preorder_reference.get(
            "strategy_version"
        )
    )

    if not expected_version:

        return {
            "ok": False,
            "reason":
                "ELIGIBILITY_STRATEGY_VERSION_MISSING",
            "entry_id":
                entry_id,
            "reference_price":
                None,
            "preorder_reference":
                preorder_reference,
        }

    if durable_version != expected_version:

        return {
            "ok": False,
            "reason":
                "PREORDER_REFERENCE_STRATEGY_VERSION_MISMATCH",
            "entry_id":
                entry_id,
            "expected_strategy_version":
                expected_version,
            "actual_strategy_version":
                durable_version,
            "reference_price":
                None,
            "preorder_reference":
                preorder_reference,
        }

    try:
        durable_price = float(
            preorder_reference.get(
                "reference_price"
            )
        )
    except Exception:
        durable_price = 0.0

    if durable_price <= 0.0:

        return {
            "ok": False,
            "reason":
                "PREORDER_REFERENCE_PRICE_INVALID",
            "entry_id":
                entry_id,
            "reference_price":
                None,
            "preorder_reference":
                preorder_reference,
        }

    return {
        "ok": True,
        "reason":
            "DURABLE_PREORDER_REFERENCE_BOUND",
        "entry_id":
            entry_id,
        "strategy_version":
            durable_version,
        "reference_price":
            durable_price,
        "reference_source":
            "GREEN7A_DURABLE_PREORDER_REFERENCE",
        "preorder_reference":
            preorder_reference,
    }



def exact_order_trade_rows(
    trade_rows,
    *,
    order_id,
    symbol=None,
):
    """
    EXACT_ORDER_ID_FILTER
    """

    wanted_order = _text(
        order_id
    )

    wanted_symbol = _upper(
        symbol
    )

    if not wanted_order:
        return []

    rows = (
        trade_rows
        if isinstance(
            trade_rows,
            list,
        )
        else []
    )

    out = []

    for row in rows:

        if not isinstance(
            row,
            dict,
        ):
            continue

        if _text(
            row.get(
                "orderId"
            )
        ) != wanted_order:
            continue

        row_symbol = _upper(
            row.get(
                "symbol"
            )
        )

        if (
            wanted_symbol
            and row_symbol
            and row_symbol != wanted_symbol
        ):
            continue

        out.append(
            dict(row)
        )

    return out


def eligible_canary_history_row(
    history_row,
    *,
    expected_strategy_version,
    deployment_tier,
):
    """
    CANARY_ONLY_SCOPE
    EXACT_STRATEGY_VERSION_FILTER
    """

    if _upper(
        deployment_tier
    ) != "CANARY":

        return {
            "ok": False,
            "reason":
                "NOT_CANARY_SCOPE",
        }

    expected = _text(
        expected_strategy_version
    )

    if not expected:

        return {
            "ok": False,
            "reason":
                "EXPECTED_STRATEGY_VERSION_MISSING",
        }

    actual = extract_strategy_version(
        history_row
    )

    if not actual:

        return {
            "ok": False,
            "reason":
                "HISTORICAL_OR_UNVERSIONED_ROW",
        }

    if actual != expected:

        return {
            "ok": False,
            "reason":
                "STRATEGY_VERSION_MISMATCH",

            "actual_strategy_version":
                actual,

            "expected_strategy_version":
                expected,
        }

    order_id = extract_order_id(
        history_row
    )

    if not order_id:

        return {
            "ok": False,
            "reason":
                "ORDER_ID_MISSING",
        }

    return {
        "ok": True,
        "reason":
            "EXACT_VERSION_CANARY_ROW",

        "strategy_version":
            actual,

        "order_id":
            order_id,
    }


def fetch_account_trades_read_only(
    client,
    *,
    symbol,
    start_time=None,
    end_time=None,
    limit=1000,
):
    """
    READ_ONLY_EXCHANGE_API

    Supports:
    - UMFutures.get_account_trades()
    - python-binance Client.futures_account_trades()

    Caller supplies an already-created authenticated read-capable client.
    This function never reads or prints API secrets.
    """

    if client is None:

        return {
            "ok": False,
            "reason":
                "CLIENT_MISSING",

            "rows": [],
        }

    symbol = _upper(
        symbol
    )

    if not symbol:

        return {
            "ok": False,
            "reason":
                "SYMBOL_MISSING",

            "rows": [],
        }

    kwargs = {
        "limit":
            max(
                1,
                min(
                    int(limit),
                    1000,
                ),
            ),
    }

    if start_time is not None:
        kwargs[
            "startTime"
        ] = int(
            start_time
        )

    if end_time is not None:
        kwargs[
            "endTime"
        ] = int(
            end_time
        )

    fn = getattr(
        client,
        "get_account_trades",
        None,
    )

    mode = (
        "UMFUTURES_GET_ACCOUNT_TRADES"
    )

    if fn is None:

        fn = getattr(
            client,
            "futures_account_trades",
            None,
        )

        mode = (
            "PYTHON_BINANCE_FUTURES_ACCOUNT_TRADES"
        )

    if fn is None:

        return {
            "ok": False,
            "reason":
                "READ_ONLY_USER_TRADES_METHOD_MISSING",

            "rows": [],
        }

    try:

        if mode == (
            "UMFUTURES_GET_ACCOUNT_TRADES"
        ):

            response = fn(
                symbol=symbol,
                **kwargs,
            )

        else:

            response = fn(
                symbol=symbol,
                **kwargs,
            )

    except Exception as exc:

        return {
            "ok": False,
            "reason":
                "USER_TRADES_READ_FAILED",

            "error_type":
                type(exc).__name__,

            # NO_SECRET_OUTPUT:
            # exception body intentionally omitted.
            "rows": [],
        }

    # Some UMFutures configurations may return a limit-usage envelope.
    if isinstance(
        response,
        dict,
    ):

        if isinstance(
            response.get("data"),
            list,
        ):
            response = response[
                "data"
            ]

    if not isinstance(
        response,
        list,
    ):

        return {
            "ok": False,
            "reason":
                "USER_TRADES_RESPONSE_INVALID",

            "rows": [],
        }

    clean_rows = [
        dict(row)
        for row in response
        if isinstance(
            row,
            dict,
        )
    ]

    return {
        "ok": True,
        "reason":
            "READ_ONLY_USER_TRADES_OK",

        "api_mode":
            mode,

        "rows":
            clean_rows,

        "row_count":
            len(
                clean_rows
            ),
    }


def collect_execution_telemetry(
    history_row,
    trade_rows,
    *,
    expected_strategy_version,
    deployment_tier,
    symbol=None,
    side=None,
    reference_price=None,
    validated_cost_policy=None,
    preorder_reference_path=None,
):
    """
    Build exact-version CANARY execution evidence.

    Durable binding:
      history entry_id
      -> durable preorder_reference
      -> exact strategy_version
      -> pre-order reference_price
      -> exact orderId fills
      -> VWAP / commission / slippage
    """

    eligibility = (
        eligible_canary_history_row(
            history_row,
            expected_strategy_version=
                expected_strategy_version,
            deployment_tier=
                deployment_tier,
        )
    )

    if not eligibility.get(
        "ok"
    ):

        return {
            "schema":
                MARKER,

            "ok":
                False,

            "decision":
                "WAIT",

            "reason":
                eligibility.get(
                    "reason",
                    "NOT_ELIGIBLE",
                ),

            "actual_fee_slippage_sane":
                False,

            "eligibility":
                eligibility,
        }

    order_id = eligibility[
        "order_id"
    ]

    reference_binding = (
        resolve_preorder_reference(
            history_row,
            eligibility,
            reference_price=
                reference_price,
            preorder_reference_path=
                preorder_reference_path,
        )
    )

    if not reference_binding.get(
        "ok"
    ):

        return {
            "schema":
                MARKER,

            "ok":
                False,

            "decision":
                "WAIT",

            "reason":
                reference_binding.get(
                    "reason",
                    "SLIPPAGE_REFERENCE_MISSING",
                ),

            "actual_fee_slippage_sane":
                False,

            "strategy_version":
                eligibility[
                    "strategy_version"
                ],

            "order_id":
                order_id,

            "entry_id":
                reference_binding.get(
                    "entry_id",
                    "",
                ),

            "reference_price":
                None,

            "preorder_reference":
                reference_binding.get(
                    "preorder_reference"
                ),
        }

    effective_reference_price = (
        reference_binding[
            "reference_price"
        ]
    )

    entry_id = _text(
        reference_binding.get(
            "entry_id"
        )
    )

    history_symbol = _upper(
        (
            history_row.get(
                "symbol"
            )
            if isinstance(
                history_row,
                dict,
            )
            else ""
        )
    )

    effective_symbol = (
        _upper(symbol)
        or history_symbol
    )

    matched = exact_order_trade_rows(
        trade_rows,
        order_id=order_id,
        symbol=effective_symbol,
    )

    if not matched:

        return {
            "schema":
                MARKER,

            "ok":
                False,

            "decision":
                "WAIT",

            "reason":
                "EXACT_ORDER_TRADE_ROWS_NOT_FOUND",

            "actual_fee_slippage_sane":
                False,

            "strategy_version":
                eligibility[
                    "strategy_version"
                ],

            "order_id":
                order_id,

            "entry_id":
                entry_id,

            "reference_price":
                effective_reference_price,

            "preorder_reference":
                reference_binding.get(
                    "preorder_reference"
                ),
        }

    evidence = execution_cost_evidence(
        matched,
        order_id=order_id,
        symbol=effective_symbol,
        side=side,
        reference_price=
            effective_reference_price,
        validated_cost_policy=
            validated_cost_policy,
    )

    reason = _text(
        evidence.get(
            "reason"
        )
    )

    return {
        "schema":
            MARKER,

        "ok":
            bool(
                evidence.get(
                    "ok"
                )
            ),

        "decision":
            evidence.get(
                "decision",
                "WAIT",
            ),

        "reason":
            reason,

        "strategy_version":
            eligibility[
                "strategy_version"
            ],

        "order_id":
            order_id,

        "entry_id":
            entry_id,

        "symbol":
            effective_symbol,

        "matched_trade_count":
            len(matched),

        "reference_price":
            effective_reference_price,

        "reference_source":
            reference_binding.get(
                "reference_source"
            ),

        "preorder_reference":
            reference_binding.get(
                "preorder_reference"
            ),

        "actual_fee_slippage_sane":
            bool(
                evidence.get(
                    "actual_fee_slippage_sane"
                )
            ),

        "execution_evidence":
            evidence,
    }
